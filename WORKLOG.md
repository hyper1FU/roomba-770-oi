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

- 2026-05-23 21:56:48  check_wiring on COM11: awake=False wake_via_dtr=False banner_bytes=0 file=20260523-215642_check_wiring.log
- 2026-05-23 21:58:35  probe_brc_lines on COM11: any_reply=False, file=20260523-215803_probe_brc_lines.log

---

## 2026-05-23 — Session 2 cont'd: woke Roomba off the dock, re-tested

User took the Roomba off the dock to wake it. Re-ran `check_wiring.py`:
**still 0 bytes**, identical to before. Sleep ruled out.

To exhaust the remaining software-side variables, wrote `scripts/probe_brc_lines.py`
which sweeps every plausible BRC wake configuration:

    line ∈ {DTR, RTS}  ×  asserted ∈ {True, False}  ×  pulse_ms ∈ {250, 1000}

All 8 combinations: **0 bytes received**, with no banner, no garbage, no anything.
Also logged the modem-status lines (CTS/DSR/RI/CD): all stayed at 0 throughout
every variant, confirming the cable has no internal loopback between the
control lines and that the BRC pin (if connected) doesn't feed back into a
status line.

### What this rules out

- Roomba in deep sleep (we woke it physically by removing it from the dock).
- Wrong baud (we tried 115200 and 19200).
- Wrong BRC line (DTR vs RTS).
- Wrong BRC polarity (asserted True or False).
- Insufficient wake pulse (we tried up to 1000 ms — 20× the documented minimum).
- USB adapter Rx/Tx tied together (loopback check passed).
- BRC mistakenly wired to a modem-status line (CTS/DSR/RI/CD never moved).

### What remains as candidate causes (hardware)

In strong order of likelihood:

1. **TX and RX swapped on the Mini-DIN side.** USB-side TX must reach Roomba
   pin 3; USB-side RX must reach Roomba pin 4. Symmetric symptom: neither
   direction works.
2. **GND not actually bonded between USB shell and Mini-DIN pin 6 or 7.**
   Without a common reference both sides see floating TTL.
3. **Broken wire inside the cable** (especially the Mini-DIN end, which is
   famously fragile on these cables).
4. **CH340 TX line not actually driving an idle high.** Easy to verify with
   a multimeter: TX-to-GND should sit at ~3.3 V (or ~5 V depending on the
   CH340 board) when nothing is being sent.

### Recommended physical-debug order

1. Multimeter (DC volts, with USB shell as GND reference):
   - Mini-DIN pin 6 or 7 ↔ USB shell: continuity / 0 V drop. Confirms GND.
   - USB-side TX ↔ USB shell: ~3.3 V (or ~5 V) idle. Confirms CH340 drives.
   - Mini-DIN pin 3 ↔ USB shell while idle: should match the above — confirms
     TX reaches Roomba.
   - Mini-DIN pin 4 ↔ USB shell while Roomba is awake: should also idle at
     ~3.3 V — confirms Roomba TX is driving its end.
2. Physically swap pin 3 and pin 4 at the Mini-DIN side, re-run
   `scripts/check_wiring.py --port COM11`. A single byte (`0..3`) returned
   proves the polarity.
3. If still nothing: try a known-good cable, or trace continuity from each
   Mini-DIN pin to its USB-side wire.

No more software-side options available. Pause this session until the cable
can be inspected with a multimeter.
- 2026-05-23 22:00:12  check_wiring on COM11: awake=False wake_via_dtr=True banner_bytes=0 file=20260523-220008_check_wiring.log

---

## 2026-05-23 — Session 3: communication established

User physically swapped Rx and Tx at the cable side and re-ran
`scripts/check_wiring.py --port COM11`. Result:

```
[loopback]   success=True
[awake-try]  success=False  no reply within 1 s.
[wake+DTR]   success=True   raw=01  banner=0 B
             OI mode reply = 1 after BRC pulse.
PASS: Rx/Tx and ground are correctly wired.
```

### What this confirms about the cable

- **Rx/Tx polarity:** the original wiring was reversed. With the swap, both
  directions now work.
- **GND:** must have already been correct (a swap alone could not have fixed
  things otherwise).
- **DTR is wired to BRC** and the polarity is normal (pyserial `dtr=True`
  drives BRC low). One DTR low pulse of 250 ms is enough to wake the robot.
- **Phase A failed (no reply without a wake pulse).** Consistent with the
  700-series sleep behavior reported in the public sources: the robot really
  is asleep, even though we removed it from the dock minutes ago.

### First observed deviation from 500-series OI

The 500-series spec says a BRC wake pulse causes the firmware to emit an ASCII
banner over TXD (`bl-start, ... Roomba by iRobot!, version 3.X.X ...`).

**Our wake produced banner = 0 bytes.** The Roomba accepted the subsequent
`Start (128)` + `Sensors (142, 35)` and replied with the correct OI mode, so
the data path works — but no banner was printed.

This could be:
  (a) a real 770 vs 500 deviation — the 700-series firmware just doesn't
      print a banner on BRC wake, OR
  (b) the robot wasn't in *deep* Off, only in Passive idle, so there was
      nothing to print.

To disambiguate we need to put it in deep Off (e.g. send `Power (133)`, or
wait 5+ min), then BRC-wake while running `scripts/wake_and_banner.py` with a
long listen window. We'll do that next session.

### Captures from this session

- `captures/20260523-215517_check_wiring.log`  (pre-swap, all silent)
- `captures/20260523-215642_check_wiring.log`  (pre-swap, after dock-out, still silent)
- `captures/20260523-215803_probe_brc_lines.log`  (pre-swap, 8-way sweep, all silent)
- `captures/20260523-220008_check_wiring.log`  (post-swap, **PASS**)

---

## 2026-05-23 — Session 4: arrow-key teleop GUI

Built `scripts/teleop_gui.py` (Tkinter) so we can drive the robot from the
keyboard without the vacuum running. The whole point is to be able to put it
on a table or floor, exercise the wheels with realistic load, and look at
sensors live — without the noise / mess of cleaning motors.

### Design choices

- **Vacuum / brushes stay off** because we never send opcodes 135 (Clean),
  134 (Spot), 136 (Max), 138 (Motors), or 144 (PWM Motors). The Roomba does
  not auto-start brushes when entering Safe mode; you have to explicitly tell
  it to.
- **Motion uses Drive Direct (145) only.** Per-wheel signed mm/s in
  [-500, 500], clamped in `roomba770/oi.py` by the new `drive_direct()`
  helper (and its companion `_signed16_be()`).
- **Safe mode**, not Full. The firmware still does cliff / wheel-drop /
  charger-attached interlocks. If a cliff fires, the robot ignores further
  motion commands until the OI is "reset" to Safe again.
- **Key freshness watchdog.** Tkinter's KeyRelease timing is unreliable —
  Windows fires it only on actual release, but X11 fires KeyRelease and a
  fresh KeyPress on every auto-repeat tick. We sidestep that by tracking a
  "last-seen" time per arrow key and treating the key as held only if its
  last KeyPress was within 120 ms. KeyRelease shoves the timestamp 80 ms
  into the past, so a real release expires in 40 ms but an immediate X11
  auto-repeat press refreshes it back. Works the same on both platforms.
- **Heartbeat.** Even when velocity hasn't changed, we re-send Drive Direct
  every ~200 ms. Some Roomba firmware revisions are reported to halt motion
  if no command has been received for ~1 s; this stays well under that.
- **Safety on shutdown.** Window close, focus loss, Quit, Escape, Q, and any
  serial exception all path through a `drive_direct(0, 0)`. A `<FocusOut>`
  binding clears all held keys so the wheels don't latch on if the user
  Alt-Tabs.
- **Telemetry at ~1 Hz** when no arrow key is held: voltage, current,
  temperature, battery %, charging state (group packet 3 = 10 bytes), plus
  bumper / wheel-drop bitfield (packet 7 = 1 byte). Skipped when actively
  driving so we don't interleave 142 replies with the next 145 send.

### Files touched

- `roomba770/oi.py`: added `_signed16_be()`, `drive_direct(right, left)`,
  `all_motors_off()`.
- `scripts/teleop_gui.py`: new.

### Not yet tested

- Did NOT launch the GUI in this session (would require driving the robot).
  Help/argparse parses, helpers import, signed-16 encoding spot-checked
  (`150 → 00 96`, `-150 → FF 6A`, `±500 boundary`, clamp at `9999 → 01 F4`).
- User to run `uv run python scripts/teleop_gui.py --port COM11` with the
  Roomba on the floor and report behavior. Watch for:
    - Window connects and shows "OI mode = 2 (safe)".
    - Each arrow drives the right wheels in the right direction.
    - Watchdog halts motion when keys are released.
    - Telemetry numbers look sane (voltage ~14-16 V, capacity > 0).
    - **No vacuum or brushes turn on at any point.**
- 2026-05-24 11:57:14  probe_sensors: 63 documented + 65 undocumented on COM11, file=20260524-115646_probe_sensors.log

---

## 2026-05-24 — Session 5: full OI investigation, 770 vs 500/600/Create-2

User confirmed the teleop GUI works and all telemetry values look correct, so
the data path is verified end-to-end. Ran the four probes in order with a
BRC wake added at the top of each (`scripts/_common.py::wake_brc`).

### Probe 1: `probe_sensors.py` (all packet IDs 0..127)

`captures/20260524-115646_probe_sensors.log`

- **Every documented packet (7..58) and every documented group (0..6, 100,
  101, 106, 107) returned exactly the documented number of bytes.** Includes
  the Create-2-only packets (43..58): left/right encoders, light bumper +
  six light-bump signals, IR char L/R, four motor currents, stasis. **The
  770 implements the full Create-2 sensor packet set, not just the 500 set.**

- **Cargo Bay (pkts 32, 33) returned all-zero** as expected — the 770 has no
  cargo connector, so these slots are present but inert.

- **Stasis (pkt 58) returned `0x02`, not `0x00` or `0x01`** as documented.
  Likely the "disabled" bit (bit 1) is set, but worth flagging as a
  documentation-vs-actual deviation.

- **Undocumented packet IDs 59..127 ALL respond.** Most return 2 bytes of
  zero, but four of them return non-trivial data on every read:
  ```
  pkt  59          1B  = 00
  pkt  60          2B  = 09 32   (= 2354)   ≈ pkt 28 (Cliff Left Signal = 2368)
  pkt  61          2B  = 09 1E   (= 2334)   ≈ pkt 31 (Cliff Right Signal = 2375)
  pkt  62          2B  = 03 45   (=  837)
  pkt  63          2B  = 00 00
  pkt  64          2B  = 03 56   (=  854)
  pkt  65          2B  = 00 00
  pkt 108         13B  = 00 (09 2A) (09 1B) (03 45) (00 00) (03 56) (00 00)
  ```
  Packet 108 is clearly a group packet aggregating 59..65 (1B + 6×2B).
  The values in 60, 61, 62, 64 are sensor-like (steady, in cliff-signal
  magnitudes). My current hypothesis: these are extra IR / wall-tracking
  signals reserved by iRobot but never documented in the OI spec. The 770
  exposes them anyway. Need a "drive while polling" experiment to confirm
  they vary with the environment.

### Probe 2: `probe_stream.py` — Stream / Pause / Resume / Query List

`captures/20260524-121939_probe_stream.log`

This is where the **opcode table I started with was wrong**. The reference I
wrote up first had `147=Stream, 148=Query List, 149=Pause/Resume`. In fact
the 500-series spec — and the 770 confirmed by experiment — uses:

| opcode | function | 770? |
| --- | --- | --- |
| 148 | Stream                | **YES** — sent `148, 3, 7, 35, 22`, got 199 valid frames in 3.0 s. |
| 149 | Query List            | **YES** — sent `149, 3, 7, 35, 22`, got exactly 4 bytes: 1B+1B+2B = pkts 7, 35, 22. |
| 150 | Pause/Resume Stream   | **YES** — `150, 0` immediately silenced the stream (0 B in 1 s); `150, 1` resumed it. |

- Frame format on 770 is `header=19, n, [pkt_id, payload]*, checksum` with
  `(sum of all bytes incl. checksum) mod 256 == 0`. Every captured frame
  passed the checksum.
- **Stream rate ~66 Hz, not 15 Hz**. 199 frames in 3.0 s = 66 fps; the
  500-series spec documents 15 Hz as the rate. The 770 firmware ships them
  far more aggressively. (Confirmed by frame count, not just bandwidth: each
  frame is 10 bytes here, so 660 B/s × 8 bits = ~5300 bps — well under the
  115200 baud limit.)
- `opcodes.py` and `docs/oi_reference.md` updated to reflect 148=Stream.

### Probe 3: `probe_opcodes.py` (safe set, with stream-pause after 148)

`captures/<latest>_probe_opcodes.log`

After patching the probe to send `150, 0` immediately after every Stream
test, every other opcode returned cleanly. Findings:

| opcode | name             | 770 result |
| ------ | ---------------- | ---------- |
| 128 | Start              | OI mode → 1 ✓ |
| 129 | Baud              | accepted (sent code 11 = 115200, no-op) ✓ |
| 131 | Safe              | OI mode → 2 ✓ |
| 132 | Full              | OI mode → 3 ✓ |
| 142 | Sensors            | works for every packet (Probe 1) |
| 148 | Stream             | works (Probe 2) |
| 152 | Script             | accepted silently |
| 153 | Play Script        | accepted silently |
| 154 | Show Script        | **0 bytes reply** — does not appear to return the stored script as documented. Either Show Script is unimplemented on the 770, or our prior Script (152) was silently rejected. |
| 155 | Wait Time          | accepted (no-op outside a script) |
| 156 | Wait Distance      | accepted |
| 157 | Wait Angle         | accepted |
| 158 | Wait Event         | accepted |
| 162 | Schedule LEDs      | accepted silently (no digit display, but firmware doesn't reject) |
| 163 | Digit LEDs Raw     | accepted silently |
| 164 | Digit LEDs ASCII   | accepted silently |
| 165 | Buttons            | accepted (sent 0 = press nothing) |
| 173 | Stop               | OI mode → **1 (Passive), NOT 0 (Off)** as Create-2 spec says. Also dumps ASCII text (see below). |

Did **not** probe 167 (Schedule) or 168 (Set Day/Time) because they would
overwrite the user's on-robot clock and schedule. Add to a later opt-in run.

### Probe 4: `capture_stop_banner.py` — the big new finding

`captures/20260524-121705_stop_banner.bin`, `..._122131_stop_banner.bin`

**In OFF mode (i.e. after `Stop`), the 770 firmware continuously prints
human-readable ASCII battery telemetry over TXD at 1 line per second.**
This is completely undocumented in any OI spec. Format:

```
bat:   min 863  sec 54  mV 18229  mA 1070  tenths-deg-C 362  mAH 2696  state 5
bat:   min 863  sec 55  mV 18202  mA 1070  tenths-deg-C 362  mAH 2696  state 5
bat:   min 863  sec 56  mV 18202  mA 1070  tenths-deg-C 362  mAH 2696  state 5
```

Fields:
- `min` and `sec`: monotonic uptime counters (`sec` rolls 0..59, `min` is
  cumulative; we saw 859..864 across captures, i.e. ~14 h since power-on).
- `mV`: instantaneous battery voltage. 16977 mV when idle on the dock with
  the OI off; jumped to 18229 mV when charging current was flowing.
- `mA`: signed instantaneous current. ~0 when idle, **+1070 mA when actively
  charging** (and presumably negative when discharging).
- `tenths-deg-C`: temperature in 0.1 °C. 362-365 ≈ 36.2-36.5 °C.
- `mAH`: battery charge (matches packet 25 = 2696 mAh).
- `state`: internal battery-state machine number. Observed **5 (actively
  charging) and 6 (idle/full)**, both outside the OI's documented Charging
  State range 0..5.

In addition to the `bat:` lines, transient event messages appear when the
robot changes state, e.g.:

```
start-charge: 2012-08-22-1754-L   \r\n
do-charging-...
```

The timestamp `2012-08-22-1754` is whatever the on-robot clock reads (which
in our case is the factory default — the user hasn't set it). The trailing
`-L` is probably "local time", and the format is `YYYY-MM-DD-HHMM`.

### Summary of 770 vs 500/600/Create-2 OI

| Aspect | 500 / Create-2 spec | 770 actual | Delta |
| --- | --- | --- | --- |
| Mini-DIN 7-pin, 115200 8N1 | yes | yes | — |
| BRC wake banner | "Roomba by iRobot! version X.Y" | **different text, and only sometimes** — see Session 7 | REVISED (was "silent") |
| Modes 0/1/2/3 | Off/Passive/Safe/Full | same | — |
| Documented opcodes 128..173 (safe ones) | all spec'd | all accepted | — |
| `Stop (173)` post-mode | Off (0) | **Passive (1)** | DEVIATION |
| `Show Script (154)` | returns stored script | **returns 0 bytes** | DEVIATION (possibly unimplemented) |
| Sensor packets 7..58 | spec'd sizes | match | — |
| Light bumper packets 45..51 | Create-2 only | **works on 770** | match Create-2 |
| `Stasis (58)` | 0/1 | **2** | DEVIATION (likely "disabled" bit) |
| Undocumented packets 59..127 | not spec'd, expected silent | **all respond**; 60/61/62/64/108 carry real data | UNDOCUMENTED FEATURE |
| `Stream (148)` rate | 15 Hz | **~66 Hz** | DEVIATION (faster) |
| OFF-mode serial output | silent | **`bat:` telemetry + event log at 1 Hz** | UNDOCUMENTED FEATURE |

### What we should not conclude (yet)

- We cannot say `Show Script` is universally broken — there could be a state
  setup issue. Worth a focused retest where we definitely store a script
  first (and the storage acks somehow), then call Show Script in Passive.
- `state 5/6` from the `bat:` log are not necessarily the same enum as OI's
  Charging State (packet 21). Worth comparing the two over a longer trace.
- We did NOT test 167 (Schedule), 168 (Set Day/Time), 144 (PWM Motors), 146
  (Drive PWM), 134/135/136 (clean cycles), 138 (Motors). The motion ones we
  can test via the teleop GUI; the cleaning / schedule ones need a separate
  opt-in session.

### Files updated this session

- `roomba770/opcodes.py`: 148/149/150 corrected; spec tag `700?` upgraded to
  `770` for packets / opcodes that have now been confirmed by experiment.
- `docs/oi_reference.md`: same corrections.
- `scripts/_common.py`: added `wake_brc()` helper.
- `scripts/probe_sensors.py`, `probe_opcodes.py`, `probe_stream.py`: call
  `wake_brc()` at the top so probes are robust to a sleeping robot.
- `scripts/probe_opcodes.py`: removed 167/168 from default safe set; fixed
  script-store payload from `[1, 153]` (causes recursive Play Script) to
  `[2, 155, 0]` (stores a 2-byte no-op script `Wait Time 0`); inserted
  `Pause Stream (150, 0)` right after probing opcode 148.
- `scripts/probe_stream.py`: corrected pause opcode to 150 (was 149).
- `scripts/capture_stop_banner.py`: new — Start, Stop, listen N seconds,
  dump everything to file. Used to discover the OFF-mode `bat:` stream.
- 2026-05-24 11:59:56  probe_opcodes: probed 18 opcodes on COM11, file=20260524-115952_probe_opcodes.log
- 2026-05-24 12:19:45  probe_stream: stream-op=148, captured=1960 bytes, file=20260524-121938_probe_stream.log
- 2026-05-24 12:20:46  probe_opcodes: probed 18 opcodes on COM11, file=20260524-122041_probe_opcodes.log
- 2026-05-24 12:21:44  capture_stop_banner on COM11: 978 bytes, file=20260524-122131_stop_banner.bin

---

## 2026-09-03 — Session 6: network pass-through (no hardware involved)

**⚠ Nothing in this session touched the robot.** All checks ran against a
**mock ESP32 + mock Roomba** on localhost, so **no capture was kept** --
`captures/` stays a record of the real 770 only.

### What changed

`Roomba.open()` now goes through `serial_for_url()`, so a script reaches the
robot either way:

```
Roomba("COM11")                        # USB-serial cable, as before
Roomba("socket://192.168.1.50:4000")   # via the ESP32 on the robot
```

The ESP32 bridges TCP to the Roomba's UART. BRC moved to a second TCP port
(data port + 1) because the ESP32 drives BRC from a GPIO, not from DTR;
`pulse_brc()` picks the right route automatically.

### The bug this uncovered

`read_available()` sized its read with `ser.in_waiting`. **pyserial's
`socket://` transport returns 1 whenever any data is pending** -- a flag, not
a count. We were reading one byte per call, about 20 B/s, against a 66 Hz
sensor stream of ~5 kB/s.

| | |
| --- | --- |
| `in_waiting` with 300+ bytes buffered | **1** |
| raw socket read, same second | 1310 bytes |
| stream through `oi.py`, before | **2.0 Hz** |
| stream through `oi.py`, after | **63.3 Hz** (raw socket: 64.5 Hz) |

`read_buffered()` replaces the arithmetic with a short read timeout, which
behaves the same on a real port and a socket.

### Verified

`probe_sensors.py --port socket://127.0.0.1:4000` exits 0 and reads every
packet, including the 770 deviations the mock reproduces (`Stasis == 2`, no
banner on BRC wake).

**Still unverified against the real robot**: everything above. The mock has
no latency (loopback) and never drops a byte, so it says nothing about how
the pass-through behaves over WiFi.

---

## Session 7 — 2026-09-03 — first run against the real robot over the ESP32 pass-through

Hardware in the loop for the first time: PC → WiFi → ESP32 (MAIN board) →
UART1 → Roomba OI. No USB-serial cable to the robot at all.

**Port string is the only change**: `--port socket://192.168.68.5:4000`.

### Throughput over WiFi

| | |
| --- | --- |
| `Stream (148)` frames in 10.0 s | **639 → 63.6 Hz** |
| unparsed bytes left over | **0** |
| `uart_ovf` on the ESP32 | **0** |
| RSSI | −33 to −35 dBm |

63.6 Hz against 64.5 Hz measured on the mock's loopback. **WiFi costs nothing
measurable in throughput**, and nothing was dropped in either direction.
The 66 Hz figure from Session 2 stands.

### The banner exists. Session 2's "silent" line was wrong.

The very first BRC pulse of the session produced this, verbatim:

```
key-wakeup
slept for 1 minutes 14 seconds
2012-08-22-1754-L
r3_orion/tags/release-1.1.7:1031 CLEAN
bootloader id: 4718 4C59 4...
```

**This answers open question 1** (the firmware fingerprint), and it is *not*
the 500-series `Roomba by iRobot!` text at all — different format entirely,
which is why "silent" and "no banner" were the wrong conclusions rather than
merely incomplete ones.

| | |
| --- | --- |
| firmware | **`r3_orion/tags/release-1.1.7:1031 CLEAN`** |
| build date | `2012-08-22-1754-L` |
| wake reason reported | `key-wakeup` |

### ...but we could not reproduce it. The trigger is still unknown.

Session 2 proposed disambiguating with `Power (133)` + a long listen. Done,
twice, and **it does not work**:

| attempt | result |
| --- | --- |
| `Power(133)`, wait 3 s, BRC 250 ms | **0 bytes** |
| `Power(133)`, wait 40 s, BRC 250 ms | **0 bytes** (also 0 bytes spontaneous during the wait) |

So `Power (133)` does **not** put the robot into whatever state prints the
banner. The one time it printed, the robot had reached that state on its own
and reported **74 seconds** of sleep.

**Leading hypothesis, untested**: there is a minimum sleep duration, somewhere
**between 40 s and 74 s**, before a wake counts as a `key-wakeup` from deep
sleep. Testing it means idling the robot without sending anything for a few
minutes and pulsing BRC — cheap, but it needs an uninterrupted robot.

**What is safe to rely on right now**: the banner may or may not appear.
**Never synchronise on it.** Use `Start (128)` → `Sensors (142, 35)` and check
the OI mode reply, exactly as `oi.py` already does. Nothing in the code
changes as a result of this finding.

### Still unverified

- Whether `Stasis (58)` really returns 2. This session's last frame read
  `{7: 0, 35: 2, 22: 14361, 21: 4, 45: 0, 17: 0, 52: 0, 53: 0}` — that 2 is
  packet **35 (OI mode = Passive)**, not Stasis. Session 2's `Stasis == 2`
  claim was **not** re-checked here and remains as recorded.
- Everything about driving. Nothing was commanded to move this session.
