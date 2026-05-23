# roomba770 — OI probe scripts

Goal: figure out which parts of the documented iRobot Roomba 500 / 600 / Create 2 Open Interface
actually work on a **Roomba 770** (700-series), and where it deviates. iRobot never published an
official OI spec for the 700 series, so we treat it as "500/600 OI + unknown deltas" and probe.

## Setup

```powershell
# Install deps into a venv managed by uv
uv sync

# List serial ports
uv run python -m scripts.list_ports
```

## Hardware

- Roomba 770 has a 7-pin **Mini-DIN** connector on the front-right, under a snap-away plastic cover.
- Pinout (looking into the socket on the robot):
  - 1, 2: Vpwr (battery voltage, unregulated, ~14-17 V)
  - 3: RxD into Roomba (3.3 V or 5 V TTL — needs level shifter from RS-232)
  - 4: TxD out of Roomba (0/5 V TTL)
  - 5: BRC (device-detect / wake pulse, active low)
  - 6, 7: GND
- Default serial: **115200 baud, 8N1, no flow control** (same as 500/600/800).

## Probes

| Script | Purpose |
| --- | --- |
| `scripts/list_ports.py`     | Enumerate available serial ports. |
| `scripts/wake_and_banner.py`| Pulse BRC low, capture any text banner the robot emits at boot/wake. |
| `scripts/probe_opcodes.py`  | Send each documented opcode, log how Roomba responds (silence / mode change / data). |
| `scripts/probe_sensors.py`  | Read every sensor packet ID 0..107, log size + raw bytes, flag unexpected. |
| `scripts/probe_stream.py`   | Try the `Stream`/`Pause Stream`/`Resume Stream`/`Query List` commands. |

All probes log to `captures/<timestamp>_<probe>.log` and append a summary line to `WORKLOG.md`.

See [WORKLOG.md](WORKLOG.md) for the running investigation notes and
[docs/oi_reference.md](docs/oi_reference.md) for the merged 500/600/Create-2 OI reference table.
