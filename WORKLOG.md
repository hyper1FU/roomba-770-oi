# Work Log — Roomba 770 OI investigation

All times in local TZ. Newest entries on top.

---

## 2026-05-23 — Session 1: scoping & project skeleton

### Background research (no hardware yet)

Goal: identify what is *known* and what is *unknown* about the Roomba 770 (700-series) Open
Interface, before writing the probe.

Key findings from public sources:

1. **iRobot never officially documented the 700-series OI.** Official OI specs exist for the 400
   series (SCI), 500-series OI, and Create 2. The 600 series is also undocumented but is widely
   reported to behave like the 500. The 700 series is documented by reverse-engineering only.
2. **Physical layer is the same as 500/600/800:** Mini-DIN 7-pin connector, 115200 baud, 8N1,
   TTL levels (3.3 / 5 V), no hardware flow control. Pin 5 is BRC (wake / device-detect, active low).
3. **Sleep behavior differs:** at least one report says the 700-series Roomba will sleep after
   ~5 min in Passive mode *even if BRC is pulsed low periodically*. Other models honor the pulse
   to stay awake. Practical workaround: pulse BRC on demand to wake before each interaction.
4. **At least one Python library (`martinschaef/roomba`) is "tested on a Roomba 770"** and uses the
   500-series opcode set including Stream (148), Drive Direct (145), and Light Bump sensor packets
   (45-51). So the 500-series command set is mostly applicable — but the only way to know which
   specific commands or sensor packets misbehave is to probe.

### Things we explicitly want the probe to answer

- **Opcodes:** which of opcodes 128..173 (full documented 500/Create-2 range) produce no error
  and which cause Roomba to drop offline / reset / ignore?
- **Sensor packets:** which packet IDs in 0..107 return data of the documented size? Are
  Light Bump packets (45-51) really present on the 770?
- **Mode commands:** does `Start (128)` actually put it in Passive? Does `Safe (131)` /
  `Full (132)` behave as documented?
- **Stream:** does `Stream (148)` actually start a streaming sensor feed at 15 Hz? Does
  `Pause/Resume Stream (150)` work?
- **Scheduling / clock commands** (`Schedule (167)`, `Set Day/Time (168)`, `Schedule LEDs (162)`,
  `Digit LEDs Raw (163)`, `Digit LEDs ASCII (164)`): documented for Create 2 — do they work on
  the 770 which doesn't have a digit display?
- **Boot banner:** when BRC is pulsed low, the 500-series emits an ASCII banner including firmware
  version. Does the 770 do the same, and what is the version string? That string is our cheapest
  fingerprint of the OI flavor.

### Project skeleton committed

- `pyproject.toml` — uv-managed project, only dep is `pyserial`.
- `roomba770/oi.py` — minimal wrapper around the serial port (open, raw `send_opcode`, `read_n`).
- `roomba770/opcodes.py` — opcode + sensor-packet tables built from the public 500 / Create 2 specs.
- `docs/oi_reference.md` — merged OI reference with per-spec "documented in" annotations.
- `scripts/list_ports.py` — port enumeration.
- `scripts/wake_and_banner.py` — pulses BRC (when wired through a DTR/RTS line) and captures the
  boot banner.
- `scripts/probe_opcodes.py` — sweeps every documented opcode and records the post-state.
- `scripts/probe_sensors.py` — sweeps every documented sensor packet ID and records size + bytes.
- `scripts/probe_stream.py` — exercises the `Stream` / `Pause Stream` / `Resume Stream` /
  `Query List` commands.

### Smoke test (no Roomba connected)

- `uv sync` — created `.venv` (CPython 3.11.9), installed `pyserial==3.5`.
- `uv run python -m roomba770.opcodes` — opcode and sensor tables dump cleanly.
- `uv run python scripts/list_ports.py` — only Bluetooth virtual COM ports
  visible at the moment (COM3..COM10). When the USB-serial cable is plugged
  in I expect a new COM device (FTDI / CP210x / CH340) to appear.
- `uv run python scripts/{wake_and_banner,probe_opcodes,probe_sensors,probe_stream}.py --help`
  all four scripts import and print help.

### Plan for first hardware session

1. Plug in the USB-to-Roomba-serial cable. Note which COM port appears (`list_ports`).
2. With the Roomba **off**, run `wake_and_banner.py --port COMx --use-dtr`. Goal:
   capture the firmware version banner verbatim. This is the cheapest fingerprint
   of the OI flavor.
3. Run `probe_sensors.py --port COMx` to map which sensor packets respond.
   Compare against the 500/Create-2 table — anything that returns the wrong size
   or no reply is interesting.
4. Run `probe_opcodes.py --port COMx` (safe-only set first). Watch for OI-mode
   loss after each opcode — that indicates the Roomba rejected it or reset.
5. Run `probe_stream.py --port COMx --stream-opcode 148` (500-series flavor),
   then re-run with `--stream-opcode 147 --try-query-list` (Create-2 flavor).
   Whichever produces well-framed 15 Hz packets with passing checksums tells us
   which OI generation the 770 implements internally.
6. Only after the safe sweeps look healthy: lift Roomba off the floor (or remove
   brushes) and run `probe_opcodes.py --allow-dangerous` to exercise motion
   commands. This will be a separate session.


