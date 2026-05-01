"""Tests for onvif-discover. Run with: python3 -m unittest discover tests/"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "onvif-discover"


def _load_module():
    loader = SourceFileLoader("onvif_discover", str(SCRIPT))
    spec = importlib.util.spec_from_loader("onvif_discover", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


od = _load_module()


PROBE_MATCHES_ONE = b"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <e:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <d:XAddrs>http://192.168.1.64/onvif/device_service</d:XAddrs>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </e:Body>
</e:Envelope>
"""

PROBE_MATCHES_MULTI = b"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <e:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <d:XAddrs>http://10.0.0.5:8000/onvif/device_service http://10.0.0.5/onvif/device_service</d:XAddrs>
      </d:ProbeMatch>
      <d:ProbeMatch>
        <d:XAddrs>http://192.168.1.10/onvif/device_service</d:XAddrs>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </e:Body>
</e:Envelope>
"""


class ParseXAddrsTests(unittest.TestCase):
    def test_single_match(self):
        urls = od.parse_xaddrs(PROBE_MATCHES_ONE)
        self.assertEqual(urls, ["http://192.168.1.64/onvif/device_service"])

    def test_multiple_matches_and_space_separated(self):
        urls = od.parse_xaddrs(PROBE_MATCHES_MULTI)
        self.assertEqual(
            urls,
            [
                "http://10.0.0.5:8000/onvif/device_service",
                "http://10.0.0.5/onvif/device_service",
                "http://192.168.1.10/onvif/device_service",
            ],
        )

    def test_garbage_returns_empty(self):
        self.assertEqual(od.parse_xaddrs(b"not xml at all"), [])

    def test_empty_returns_empty(self):
        self.assertEqual(od.parse_xaddrs(b""), [])

    def test_no_probe_matches(self):
        envelope = (
            b'<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope">'
            b'<e:Body/></e:Envelope>'
        )
        self.assertEqual(od.parse_xaddrs(envelope), [])


class SortUrlsTests(unittest.TestCase):
    def test_sorts_by_ipv4_numeric(self):
        urls = [
            "http://192.168.1.10/onvif/device_service",
            "http://10.0.0.5/onvif/device_service",
            "http://192.168.1.2/onvif/device_service",
        ]
        self.assertEqual(
            od.sort_urls(urls),
            [
                "http://10.0.0.5/onvif/device_service",
                "http://192.168.1.2/onvif/device_service",
                "http://192.168.1.10/onvif/device_service",
            ],
        )

    def test_dedupes(self):
        urls = ["http://1.2.3.4/x", "http://1.2.3.4/x", "http://1.2.3.4/y"]
        self.assertEqual(
            od.sort_urls(urls),
            ["http://1.2.3.4/x", "http://1.2.3.4/y"],
        )

    def test_port_secondary_key(self):
        urls = [
            "http://192.168.1.1:8080/x",
            "http://192.168.1.1/x",
            "http://192.168.1.1:80/x",
        ]
        self.assertEqual(
            od.sort_urls(urls),
            [
                "http://192.168.1.1/x",
                "http://192.168.1.1:80/x",
                "http://192.168.1.1:8080/x",
            ],
        )

    def test_hostname_after_ipv4(self):
        urls = ["http://camera.local/x", "http://192.168.1.1/x"]
        self.assertEqual(
            od.sort_urls(urls),
            ["http://192.168.1.1/x", "http://camera.local/x"],
        )


class BuildProbeTests(unittest.TestCase):
    def test_probe_well_formed_and_contains_nvt(self):
        body = od.build_probe()
        self.assertIn(b"NetworkVideoTransmitter", body)
        # Each probe must have a fresh MessageID
        a = od.build_probe()
        b = od.build_probe()
        self.assertNotEqual(a, b)


class DiscoverRetransmitTests(unittest.TestCase):
    """Verify that discover() sends multiple probes within the timeout window.

    We intercept sendto() via an in-process subclass so we don't depend on
    actual multicast (which doesn't work in containers / WSL2)."""

    def test_sends_default_three_probes(self):
        import socket as _socket
        sent = []
        original_socket = _socket.socket

        class FakeSock:
            def __init__(self, *a, **kw):
                self._real = original_socket(*a, **kw)
            def setsockopt(self, *a, **kw): return self._real.setsockopt(*a, **kw)
            def settimeout(self, t): return self._real.settimeout(t)
            def bind(self, addr): return self._real.bind(addr)
            def close(self): return self._real.close()
            def sendto(self, data, addr):
                sent.append(addr)
                # don't actually send: silently discard
                return len(data)
            def recvfrom(self, n):
                # Always block until the next settimeout fires.
                raise TimeoutError()

        _socket.socket = FakeSock
        try:
            urls = od.discover(timeout=1.6, bind_addr="127.0.0.1", verbose=False)
        finally:
            _socket.socket = original_socket
        self.assertEqual(urls, [])
        # 3 probes spaced by 0.5s should fit comfortably in 1.6s.
        self.assertEqual(len(sent), od.DEFAULT_PROBE_RETRIES)
        for addr in sent:
            self.assertEqual(addr, (od.WS_DISCOVERY_GROUP, od.WS_DISCOVERY_PORT))


class WriteOutputTests(unittest.TestCase):
    def test_writes_to_file(self):
        urls = ["http://1.1.1.1/x", "http://2.2.2.2/y"]
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "out.txt")
            rc = od.write_output(urls, target)
            self.assertEqual(rc, 0)
            with open(target, encoding="utf-8") as f:
                self.assertEqual(f.read(), "http://1.1.1.1/x\nhttp://2.2.2.2/y\n")

    def test_unwritable_path(self):
        rc = od.write_output(["http://x"], "/nonexistent-dir-xyz/out.txt")
        self.assertEqual(rc, 1)


class CliTests(unittest.TestCase):
    """End-to-end CLI tests that don't touch the network."""

    def _run(self, *args, timeout=10):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True, timeout=timeout,
        )

    def test_version(self):
        r = self._run("-V")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), f"{od.PROG} {od.VERSION}")

    def test_help(self):
        r = self._run("-h")
        self.assertEqual(r.returncode, 0)
        self.assertIn("WS-Discovery", r.stdout)
        # Help goes to stdout, not stderr.
        self.assertEqual(r.stderr, "")

    def test_invalid_timeout(self):
        r = self._run("-t", "0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("timeout", r.stderr)


if __name__ == "__main__":
    unittest.main()
