# Roomba OI reference (merged 500 / 600 / Create 2 / 700)

Source documents:
- iRobot Roomba SCI Spec (400 series, ROI/SCI, 57600 baud).
- iRobot Roomba 500 Open Interface Spec (500 series, 115200 baud).
- iRobot Create 2 Open Interface Spec (Create 2 / 600 series electronics, 115200 baud).
- Reverse-engineering reports for 700 series (e.g. martinschaef/roomba, robotreviews threads).
  iRobot never published an official 700-series OI document.

For each entry below, `Spec` says which spec(s) document it:

- `SCI` — 400 series Serial Command Interface
- `500` — 500-series Open Interface (Roomba 510/530/560/etc.)
- `C2`  — Create 2 OI
- `700?` — believed to work on 700-series per third-party reports, **must be verified by probe**

If the entry is missing `700?`, treat that command/packet as **unknown on the 770 until probed**.

---

## Physical & link layer

| Property | Value |
| --- | --- |
| Connector | Mini-DIN 7-pin, front-right under cover (700 series). |
| Baud rate | 115200 default (configurable down to 19200 via `Baud (129)` on 500+). |
| Frame | 8 data bits, 1 stop bit, no parity, no flow control. |
| TTL voltage | 3.3 V to 5 V tolerant — level-shift from RS-232. |
| BRC pin | Pin 5. Pulse low ≥ 50 ms to wake from sleep. On 500/600 also keeps awake when pulsed periodically; 700 reportedly sleeps anyway after 5 min in Passive. |

Pinout (looking into Roomba socket):

```
  2 1
 4   3
 6 5 7
```

| Pin | Signal | Direction | Notes |
| --- | --- | --- | --- |
| 1, 2 | Vpwr | out | Battery+ (unregulated, ~14-17 V). |
| 3 | RXD | in  | Serial in to Roomba (TTL). |
| 4 | TXD | out | Serial out from Roomba (TTL). |
| 5 | BRC | in  | Device-detect / wake, active low. |
| 6, 7 | GND | — | Common ground. |

---

## Opcode reference (commands sent TO Roomba)

| Opcode | Mnemonic | Data bytes | Min mode | Spec | Notes |
| --- | --- | --- | --- | --- | --- |
| 128 | Start | 0 | Off | SCI, 500, C2, 700? | Enters Passive. Must be the first command after reset / power-on. |
| 129 | Baud | 1 | Passive | SCI, 500, C2, 700? | Code 0=300 ... 11=115200. |
| 130 | Control | 0 | Passive | SCI | Deprecated alias of Safe on 500+. |
| 131 | Safe | 0 | Passive | 500, C2, 700? | Enters Safe mode (cliff/wheel-drop/charger-attached safeties enabled). |
| 132 | Full | 0 | Safe | 500, C2, 700? | Disables safeties. |
| 133 | Power | 0 | Passive | 500, C2, 700? | Puts robot into Off (deep sleep). |
| 134 | Spot | 0 | Passive | 500, C2, 700? | Starts a Spot cleaning cycle. |
| 135 | Clean | 0 | Passive | 500, C2, 700? | Starts a normal cleaning cycle. |
| 136 | Max | 0 | Passive | 500, C2, 700? | Starts Max-time cleaning cycle. |
| 137 | Drive | 4 | Safe | 500, C2, 700? | velocity_hi/lo, radius_hi/lo. |
| 138 | Motors | 1 | Safe | 500, C2, 700? | Bitfield: side-brush, vacuum, main-brush, side-brush dir, main-brush dir. |
| 139 | LEDs | 3 | Safe | 500, C2, 700? | LED bits, clean-color, clean-intensity. |
| 140 | Song | 2+2N | Passive | 500, C2, 700? | song#, length, [note,duration]*N. |
| 141 | Play | 1 | Safe | 500, C2, 700? | song#. |
| 142 | Sensors | 1 | Passive | 500, C2, 700? | Single packet read. |
| 143 | Seek Dock | 0 | Passive | 500, C2, 700? | |
| 144 | PWM Motors | 3 | Safe | 500, C2 | side-brush / vacuum / main-brush PWM. **Unknown on 770.** |
| 145 | Drive Direct | 4 | Safe | 500, C2, 770 | Confirmed on 770 (teleop GUI). |
| 146 | Drive PWM | 4 | Safe | C2 | right_pwm_hi/lo, left_pwm_hi/lo. **Unknown on 770.** |
| 148 | Stream | 1+N | Passive | 500, C2, **770** | N then N packet IDs. Frame: `19, n, [pkt_id, bytes]*, checksum`. On 770 frames arrive much faster than the documented 15 Hz (closer to 50-60 Hz). Confirmed Session 5. |
| 149 | Query List | 1+N | Passive | 500, C2 | One-shot. **Unknown on 770**; could not test cleanly in the same session as 148. |
| 150 | Pause/Resume Stream | 1 | Passive | 500, C2 | 0 = pause, 1 = resume. **Unknown on 770**. |
| 152 | Script | 1+N | Passive | 500, C2 | Store a script of up to 100 bytes. |
| 153 | Play Script | 0 | Passive | 500, C2 | |
| 154 | Show Script | 0 | Passive | 500, C2 | Returns the stored script. |
| 155 | Wait Time | 1 | Passive | 500, C2 | tenths of a second. |
| 156 | Wait Distance | 2 | Passive | 500, C2 | signed mm. |
| 157 | Wait Angle | 2 | Passive | 500, C2 | signed deg. |
| 158 | Wait Event | 1 | Passive | 500, C2 | signed event id. |
| 162 | Schedule LEDs | 2 | Safe | C2 | weekday-bits, scheduling-LEDs-bits. **Unlikely to do anything visible on 770 (no digit LEDs).** |
| 163 | Digit LEDs Raw | 4 | Safe | C2 | 4 segment-byte digits. **Unlikely on 770.** |
| 164 | Digit LEDs ASCII | 4 | Safe | C2 | 4 ASCII chars. **Unlikely on 770.** |
| 165 | Buttons | 1 | Passive | C2, 700? | Press buttons by bitmask programmatically. |
| 167 | Schedule | 15 | Passive | C2 | Set the weekly cleaning schedule. **Probe — 770 has scheduling in firmware.** |
| 168 | Set Day/Time | 3 | Passive | C2 | day, hour, minute. **Probe — 770 has a clock.** |
| 173 | Stop | 0 | Passive | C2 | Stops the OI (returns to "off" but stays awake). |

Boldfaced "Unknown on 770" rows are the ones we most want the probe to clarify.

---

## Sensor packet reference (commands 142 / 148 / 149 reply payloads)

Group packets (sum of constituent packets):

| ID | Contains packets | Total bytes | Spec |
| --- | --- | --- | --- |
| 0  | 7..26   | 26 | 500, C2 |
| 1  | 7..16   | 10 | 500, C2 |
| 2  | 17..20  | 6  | 500, C2 |
| 3  | 21..26  | 10 | 500, C2 |
| 4  | 27..34  | 14 | 500, C2 |
| 5  | 35..42  | 12 | 500, C2 |
| 6  | 7..42   | 52 | 500, C2 |
| 100 | 7..58  | 80 | C2 (all packets) |
| 101 | 43..58 | 28 | C2 |
| 106 | 46..51 | 12 | C2 (all light-bump) |
| 107 | 54..58 | 9  | C2 (encoders + motor currents) |

Individual packets:

| ID | Name | Bytes | Range | Spec |
| --- | --- | --- | --- | --- |
| 7  | Bumps & Wheel Drops             | 1 | bitfield | 500, C2 |
| 8  | Wall                            | 1 | 0/1 | 500, C2 |
| 9  | Cliff Left                      | 1 | 0/1 | 500, C2 |
| 10 | Cliff Front Left                | 1 | 0/1 | 500, C2 |
| 11 | Cliff Front Right               | 1 | 0/1 | 500, C2 |
| 12 | Cliff Right                     | 1 | 0/1 | 500, C2 |
| 13 | Virtual Wall                    | 1 | 0/1 | 500, C2 |
| 14 | Wheel Overcurrents              | 1 | bitfield | 500, C2 |
| 15 | Dirt Detect                     | 1 | 0..255 | 500, C2 |
| 16 | Unused                          | 1 | always 0 | 500, C2 |
| 17 | Infrared Char Omni              | 1 | byte | 500, C2 |
| 18 | Buttons                         | 1 | bitfield | 500, C2 |
| 19 | Distance (since last read)      | 2 | signed mm  | 500, C2 — drift-prone, treat as advisory. |
| 20 | Angle (since last read)         | 2 | signed deg | 500, C2 — drift-prone. |
| 21 | Charging State                  | 1 | enum 0..5 | 500, C2 |
| 22 | Voltage                         | 2 | mV     | 500, C2 |
| 23 | Current                         | 2 | signed mA | 500, C2 |
| 24 | Temperature                     | 1 | signed °C | 500, C2 |
| 25 | Battery Charge                  | 2 | mAh | 500, C2 |
| 26 | Battery Capacity                | 2 | mAh | 500, C2 |
| 27 | Wall Signal                     | 2 | 0..1023 | 500, C2 |
| 28 | Cliff Left Signal               | 2 | 0..4095 | 500, C2 |
| 29 | Cliff Front Left Signal         | 2 | 0..4095 | 500, C2 |
| 30 | Cliff Front Right Signal        | 2 | 0..4095 | 500, C2 |
| 31 | Cliff Right Signal              | 2 | 0..4095 | 500, C2 |
| 32 | Cargo Bay Digital Inputs        | 1 | bitfield | 500 (Create) |
| 33 | Cargo Bay Analog Input          | 2 | 0..1023 | 500 (Create) |
| 34 | Charging Sources Available      | 1 | bitfield | 500, C2 |
| 35 | OI Mode                         | 1 | enum 0..3 | 500, C2 |
| 36 | Song Number                     | 1 | 0..15 | 500, C2 |
| 37 | Song Playing                    | 1 | 0/1 | 500, C2 |
| 38 | Number of Stream Packets        | 1 | count | 500, C2 |
| 39 | Requested Velocity              | 2 | signed mm/s | 500, C2 |
| 40 | Requested Radius                | 2 | signed mm   | 500, C2 |
| 41 | Requested Right Velocity        | 2 | signed mm/s | 500, C2 |
| 42 | Requested Left Velocity         | 2 | signed mm/s | 500, C2 |
| 43 | Left Encoder Counts             | 2 | counts | C2 (added) |
| 44 | Right Encoder Counts            | 2 | counts | C2 (added) |
| 45 | Light Bumper                    | 1 | bitfield | C2 — **martinschaef reports works on 770; probe.** |
| 46 | Light Bump Left Signal          | 2 | 0..4095 | C2 |
| 47 | Light Bump Front-Left Signal    | 2 | 0..4095 | C2 |
| 48 | Light Bump Center-Left Signal   | 2 | 0..4095 | C2 |
| 49 | Light Bump Center-Right Signal  | 2 | 0..4095 | C2 |
| 50 | Light Bump Front-Right Signal   | 2 | 0..4095 | C2 |
| 51 | Light Bump Right Signal         | 2 | 0..4095 | C2 |
| 52 | IR Char Left                    | 1 | byte | C2 |
| 53 | IR Char Right                   | 1 | byte | C2 |
| 54 | Left Motor Current              | 2 | signed mA | C2 |
| 55 | Right Motor Current             | 2 | signed mA | C2 |
| 56 | Main Brush Motor Current        | 2 | signed mA | C2 |
| 57 | Side Brush Motor Current        | 2 | signed mA | C2 |
| 58 | Stasis                          | 1 | 0/1 | C2 |

---

## Open questions for the probe

1. Does the 770 emit a firmware/version banner over TXD when BRC is pulsed low or at power-on?
   If so, capture verbatim — that is the cheapest model fingerprint.
2. Of opcodes 144 / 146 / 147 / 149 / 162-164 / 167 / 168 / 173, which actually work on the 770?
3. Of sensor packets 43..58 (Create 2 additions), which return data of the documented size?
4. Does packet ID 100 (all sensors) work, or does the 770 only know 0..6?
5. After `Stop (173)`, does the 770 actually return to passive without going to deep sleep?
6. Does `Set Day/Time (168)` change the on-robot clock used by the schedule?
