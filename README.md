# onvif-discover

Discover ONVIF devices on the local network via WS-Discovery and print their
device service URLs, one per line.

## Install

    chmod +x onvif-discover
    cp onvif-discover ~/.local/bin/    # or /usr/local/bin/

## Usage

Basic invocation (multicast Probe, 3 s wait):

    onvif-discover

Wait longer on a noisy or slow network, write to a file:

    onvif-discover -t 6 -o cameras.txt

Pipe straight into the next stage of the chain:

    onvif-discover | xargs -I{} onvif-rtsp --user admin --password segreta {}

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `-t`, `--timeout SECONDS` | `3.0` | how long to listen for ProbeMatches replies |
| `-b`, `--bind ADDR` | `0.0.0.0` | local IPv4 to send the Probe from (set this to pick a specific NIC) |
| `-o`, `--output FILE` | stdout | write URLs to FILE instead of stdout |
| `-v`, `--verbose` | off | log progress and replies on stderr |
| `-V`, `--version` | | print version and exit |
| `-h`, `--help` | | show help and exit |

Output on stdout is exactly one URL per line, sorted by IPv4 address (numeric
octet order), then by port. Hostnames sort after IPv4 addresses. Duplicates are
removed.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success (zero or more devices found) |
| 1 | usage error (bad flag, unwritable `-o` file, `--timeout <= 0`) |
| 2 | network error (socket bind/send failed) |
| 130 | interrupted with Ctrl-C |

## Dependencies

- Python 3.8+ (stdlib only — no third-party packages)
- A network where UDP multicast to `239.255.255.250:3702` is allowed

## Place in the chain

`onvif-discover` is the **first** script of the chain. Its output (one device
service URL per line) is the input expected by `onvif-rtsp`:

    onvif-discover → onvif-rtsp → go2rtc-gen → rtsp-play / rtsp-record → footage-merge
