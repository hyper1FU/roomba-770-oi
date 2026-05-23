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


- 2026-05-23 21:55:23  check_wiring on COM11: awake=False wake_via_dtr=False banner_bytes=0 file=20260523-215517_check_wiring.log

---

## 2026-05-23 — Session 2: first hardware connection (CH340 USB-Serial, COM11)

### What we observed

- The USB-Serial dongle enumerates as `USB-SERIAL CH340 (COM11)` with VID:PID
  `1A86:7523`. (See `list_ports` output captured this session.)
- Ran `scripts/check_wiring.py --port COM11`:
  - **Loopback** test (write `0xA5`, read back): no echo. Good — the dongle's
    Rx and Tx are not tied together, so any byte we read in later phases really
    came from the Roomba.
  - **Phase A** (assume awake, 115200): `Start (128)` then `Sensors (142, 35)` →
    **0 bytes** in 1 s.
  - **Phase B** (BRC wake via DTR low 250 ms): no boot banner at all; OI-mode
    query again returned **0 bytes**.
  - **Phase C** (fallback 19200 baud): also 0 bytes.
- Captured log: `captures/20260523-215517_check_wiring.log`.

### Interpretation

Zero bytes in either direction, in any phase, when the loopback test confirms the
USB adapter itself is healthy. That rules out:

- Adapter dead / driver broken (loopback would have showed garbage).
- Adapter Rx-Tx shorted internally (loopback would have echoed).
- Wrong baud as the *only* fault (we tried both documented bauds).

That leaves these candidates, in order of likelihood:

1. **Rx and Tx are swapped at the Mini-DIN side.** Cable TX should drive
   Roomba *pin 3* (Roomba's RxD); cable RX should listen on Roomba *pin 4*
   (Roomba's TxD). If the cable plug is wired the other way around, neither
   direction works — exactly what we see.
2. **Ground not actually connected.** Without a common GND between the dongle
   and the Roomba (Mini-DIN pin 6 or 7), the TTL levels float and nothing is
   decoded on either side.
3. **BRC line not connected at all (or wired to a non-controllable pin).** The
   robot is still in deep Off mode and our DTR pulse is going nowhere. We'd
   expect at least a boot banner if BRC worked; we got 0 bytes.
4. **Roomba is asleep at the firmware level beyond what BRC alone can wake.**
   Possible on a 700-series after a long idle; usually fixed by pressing the
   physical CLEAN button on the robot first to wake it, then connecting.
5. TTL voltage mismatch / damaged input — only if the dongle is actually a
   true RS-232 (±12 V) one. CH340 boards are TTL by default, so unlikely.

### Next steps

- Press the CLEAN button on the Roomba once (LEDs should light up briefly).
  Then re-run `scripts/check_wiring.py --port COM11`. If we now get a reply,
  the wiring is fine and the robot was just deep-asleep.
- If still 0 bytes: physically swap the Rx and Tx wires at the Mini-DIN
  connector, then rerun. A single byte (0..3) returned proves Rx/Tx polarity.
- If still 0 bytes after both: confirm GND continuity with a multimeter from
  the USB shell to Mini-DIN pin 6 or 7.

