"""Opcode and sensor-packet tables for Roomba OI.

Built from the public 500-series OI spec and Create 2 OI spec. The 700-series is
NOT officially documented by iRobot — the `spec` field on each entry tells you which
documents the entry is taken from. Anything tagged ``700?`` is reported to work on
the 700 series by third-party libraries but should still be re-verified by the
probe scripts in this repo.

Run `python -m roomba770.opcodes` to dump the tables as text.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Opcode:
    code: int
    name: str
    data_bytes: int  # fixed number of data bytes after the opcode, or -1 if variable
    min_mode: str    # "off", "passive", "safe"
    spec: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class SensorPacket:
    pid: int
    name: str
    size: int  # bytes in the reply
    signed: bool
    spec: tuple[str, ...]
    notes: str = ""


# Modes -----------------------------------------------------------------------

MODE_OFF = 0
MODE_PASSIVE = 1
MODE_SAFE = 2
MODE_FULL = 3

MODE_NAMES = {0: "off", 1: "passive", 2: "safe", 3: "full"}


# Opcodes ---------------------------------------------------------------------

OPCODES: list[Opcode] = [
    Opcode(128, "Start",         0, "off",     ("SCI", "500", "C2", "700?"),
           "Enters Passive. Must be first command after reset/power-on."),
    Opcode(129, "Baud",          1, "passive", ("SCI", "500", "C2", "700?"),
           "0=300 ... 11=115200."),
    Opcode(130, "Control",       0, "passive", ("SCI",),
           "Deprecated; on 500+ behaves like Safe."),
    Opcode(131, "Safe",          0, "passive", ("500", "C2", "700?")),
    Opcode(132, "Full",          0, "safe",    ("500", "C2", "700?")),
    Opcode(133, "Power",         0, "passive", ("500", "C2", "700?"),
           "Puts robot to Off (deep sleep)."),
    Opcode(134, "Spot",          0, "passive", ("500", "C2", "700?")),
    Opcode(135, "Clean",         0, "passive", ("500", "C2", "700?")),
    Opcode(136, "Max",           0, "passive", ("500", "C2", "700?")),
    Opcode(137, "Drive",         4, "safe",    ("500", "C2", "700?"),
           "velocity_hi velocity_lo radius_hi radius_lo, all signed."),
    Opcode(138, "Motors",        1, "safe",    ("500", "C2", "700?"),
           "Bitfield: side-brush, vacuum, main-brush, sb-dir, mb-dir."),
    Opcode(139, "LEDs",          3, "safe",    ("500", "C2", "700?"),
           "leds-bitfield, clean-color (0-255), clean-intensity (0-255)."),
    Opcode(140, "Song",         -1, "passive", ("500", "C2", "700?"),
           "song# length [note duration]*length. 2 + 2*length bytes total."),
    Opcode(141, "Play",          1, "safe",    ("500", "C2", "700?"),
           "song#."),
    Opcode(142, "Sensors",       1, "passive", ("500", "C2", "700?"),
           "Single packet ID; reply size depends on packet."),
    Opcode(143, "Seek Dock",     0, "passive", ("500", "C2", "700?")),
    Opcode(144, "PWM Motors",    3, "safe",    ("500", "C2"),
           "main-brush PWM (signed -127..127), side-brush PWM (signed -127..127), "
           "vacuum PWM (unsigned 0..127). UNKNOWN on 770."),
    Opcode(145, "Drive Direct",  4, "safe",    ("500", "C2", "700?"),
           "right_hi right_lo left_hi left_lo, signed mm/s."),
    Opcode(146, "Drive PWM",     4, "safe",    ("C2",),
           "right_pwm_hi right_pwm_lo left_pwm_hi left_pwm_lo. UNKNOWN on 770."),
    Opcode(148, "Stream",       -1, "passive", ("500", "C2", "770"),
           "N then N packet IDs. Streams frames of header 19, n, [pkt_id, bytes]*, "
           "checksum (sum mod 256 == 0). Confirmed on 770 (Session 5)."),
    Opcode(149, "Query List",   -1, "passive", ("500", "C2"),
           "N then N packet IDs; replies once with concatenated packets. "
           "UNKNOWN on 770 — opcode 148 starts a stream there, so we couldn't "
           "easily test 149 in the same session."),
    Opcode(150, "Pause/Resume Stream", 1, "passive", ("500", "C2"),
           "0=pause, 1=resume. UNKNOWN on 770 — to verify."),
    Opcode(152, "Script",       -1, "passive", ("500", "C2"),
           "N then N bytes of script."),
    Opcode(153, "Play Script",   0, "passive", ("500", "C2")),
    Opcode(154, "Show Script",   0, "passive", ("500", "C2")),
    Opcode(155, "Wait Time",     1, "passive", ("500", "C2"),
           "0..255 in tenths of a second."),
    Opcode(156, "Wait Distance", 2, "passive", ("500", "C2"),
           "signed mm."),
    Opcode(157, "Wait Angle",    2, "passive", ("500", "C2"),
           "signed deg."),
    Opcode(158, "Wait Event",    1, "passive", ("500", "C2"),
           "signed event id."),
    Opcode(162, "Schedule LEDs", 2, "safe",    ("C2",),
           "weekday-bits, scheduling-LEDs-bits. UNKNOWN on 770 (no digit LEDs)."),
    Opcode(163, "Digit LEDs Raw", 4, "safe",   ("C2",),
           "Four 7-segment digit bytes. UNKNOWN on 770."),
    Opcode(164, "Digit LEDs ASCII", 4, "safe", ("C2",),
           "Four ASCII chars. UNKNOWN on 770."),
    Opcode(165, "Buttons",       1, "passive", ("C2", "700?"),
           "Bitfield to press buttons programmatically."),
    Opcode(167, "Schedule",     15, "passive", ("C2",),
           "Sets weekly cleaning schedule. UNKNOWN on 770."),
    Opcode(168, "Set Day/Time",  3, "passive", ("C2",),
           "day, hour, minute. UNKNOWN on 770."),
    Opcode(173, "Stop",          0, "passive", ("C2",),
           "Stops the OI. UNKNOWN on 770."),
]


OPCODE_BY_NAME: dict[str, Opcode] = {op.name: op for op in OPCODES}
OPCODE_BY_CODE: dict[int, Opcode] = {op.code: op for op in OPCODES}


# Sensor packets --------------------------------------------------------------

SENSOR_PACKETS: list[SensorPacket] = [
    # Group packets (replies are the concatenation of their constituents)
    SensorPacket(0,   "Group 7..26",   26, False, ("500", "C2", "700?")),
    SensorPacket(1,   "Group 7..16",   10, False, ("500", "C2", "700?")),
    SensorPacket(2,   "Group 17..20",   6, False, ("500", "C2", "700?")),
    SensorPacket(3,   "Group 21..26",  10, False, ("500", "C2", "700?")),
    SensorPacket(4,   "Group 27..34",  14, False, ("500", "C2", "700?")),
    SensorPacket(5,   "Group 35..42",  12, False, ("500", "C2", "700?")),
    SensorPacket(6,   "Group 7..42",   52, False, ("500", "C2", "700?")),
    SensorPacket(100, "Group 7..58",   80, False, ("C2",), "UNKNOWN on 770."),
    SensorPacket(101, "Group 43..58",  28, False, ("C2",), "UNKNOWN on 770."),
    SensorPacket(106, "Group 46..51",  12, False, ("C2",), "All light-bump signals."),
    SensorPacket(107, "Group 54..58",   9, False, ("C2",), "Encoders + motor currents + stasis."),

    # Individual packets
    SensorPacket(7,  "Bumps & Wheel Drops",        1, False, ("500", "C2", "700?")),
    SensorPacket(8,  "Wall",                       1, False, ("500", "C2", "700?")),
    SensorPacket(9,  "Cliff Left",                 1, False, ("500", "C2", "700?")),
    SensorPacket(10, "Cliff Front Left",           1, False, ("500", "C2", "700?")),
    SensorPacket(11, "Cliff Front Right",          1, False, ("500", "C2", "700?")),
    SensorPacket(12, "Cliff Right",                1, False, ("500", "C2", "700?")),
    SensorPacket(13, "Virtual Wall",               1, False, ("500", "C2", "700?")),
    SensorPacket(14, "Wheel Overcurrents",         1, False, ("500", "C2", "700?")),
    SensorPacket(15, "Dirt Detect",                1, False, ("500", "C2", "700?")),
    SensorPacket(16, "Unused",                     1, False, ("500", "C2", "700?")),
    SensorPacket(17, "Infrared Char Omni",         1, False, ("500", "C2", "700?")),
    SensorPacket(18, "Buttons",                    1, False, ("500", "C2", "700?")),
    SensorPacket(19, "Distance",                   2, True,  ("500", "C2", "700?")),
    SensorPacket(20, "Angle",                      2, True,  ("500", "C2", "700?")),
    SensorPacket(21, "Charging State",             1, False, ("500", "C2", "700?")),
    SensorPacket(22, "Voltage",                    2, False, ("500", "C2", "700?")),
    SensorPacket(23, "Current",                    2, True,  ("500", "C2", "700?")),
    SensorPacket(24, "Temperature",                1, True,  ("500", "C2", "700?")),
    SensorPacket(25, "Battery Charge",             2, False, ("500", "C2", "700?")),
    SensorPacket(26, "Battery Capacity",           2, False, ("500", "C2", "700?")),
    SensorPacket(27, "Wall Signal",                2, False, ("500", "C2", "700?")),
    SensorPacket(28, "Cliff Left Signal",          2, False, ("500", "C2", "700?")),
    SensorPacket(29, "Cliff Front Left Signal",    2, False, ("500", "C2", "700?")),
    SensorPacket(30, "Cliff Front Right Signal",   2, False, ("500", "C2", "700?")),
    SensorPacket(31, "Cliff Right Signal",         2, False, ("500", "C2", "700?")),
    SensorPacket(32, "Cargo Bay Digital Inputs",   1, False, ("500",), "Create-only on 500."),
    SensorPacket(33, "Cargo Bay Analog Input",     2, False, ("500",), "Create-only on 500."),
    SensorPacket(34, "Charging Sources Available", 1, False, ("500", "C2", "700?")),
    SensorPacket(35, "OI Mode",                    1, False, ("500", "C2", "700?")),
    SensorPacket(36, "Song Number",                1, False, ("500", "C2", "700?")),
    SensorPacket(37, "Song Playing",               1, False, ("500", "C2", "700?")),
    SensorPacket(38, "Number of Stream Packets",   1, False, ("500", "C2", "770")),
    SensorPacket(39, "Requested Velocity",         2, True,  ("500", "C2", "700?")),
    SensorPacket(40, "Requested Radius",           2, True,  ("500", "C2", "700?")),
    SensorPacket(41, "Requested Right Velocity",   2, True,  ("500", "C2", "700?")),
    SensorPacket(42, "Requested Left Velocity",    2, True,  ("500", "C2", "700?")),
    SensorPacket(43, "Left Encoder Counts",        2, False, ("C2",)),
    SensorPacket(44, "Right Encoder Counts",       2, False, ("C2",)),
    SensorPacket(45, "Light Bumper",               1, False, ("C2",), "Reported working on 770 by martinschaef."),
    SensorPacket(46, "Light Bump Left Signal",            2, False, ("C2",)),
    SensorPacket(47, "Light Bump Front-Left Signal",      2, False, ("C2",)),
    SensorPacket(48, "Light Bump Center-Left Signal",     2, False, ("C2",)),
    SensorPacket(49, "Light Bump Center-Right Signal",    2, False, ("C2",)),
    SensorPacket(50, "Light Bump Front-Right Signal",     2, False, ("C2",)),
    SensorPacket(51, "Light Bump Right Signal",           2, False, ("C2",)),
    SensorPacket(52, "IR Char Left",               1, False, ("C2",)),
    SensorPacket(53, "IR Char Right",              1, False, ("C2",)),
    SensorPacket(54, "Left Motor Current",         2, True,  ("C2",)),
    SensorPacket(55, "Right Motor Current",        2, True,  ("C2",)),
    SensorPacket(56, "Main Brush Motor Current",   2, True,  ("C2",)),
    SensorPacket(57, "Side Brush Motor Current",   2, True,  ("C2",)),
    SensorPacket(58, "Stasis",                     1, False, ("C2",)),
]


SENSOR_BY_ID: dict[int, SensorPacket] = {p.pid: p for p in SENSOR_PACKETS}


def _dump() -> None:
    print(f"{'opcode':>6}  {'name':<22} {'data':>4}  {'mode':<8} {'spec':<22} notes")
    for op in OPCODES:
        print(f"{op.code:>6}  {op.name:<22} {op.data_bytes:>4}  {op.min_mode:<8} "
              f"{','.join(op.spec):<22} {op.notes}")
    print()
    print(f"{'pkt':>4}  {'name':<32} {'sz':>3} {'sgn':<4} {'spec':<22} notes")
    for pk in SENSOR_PACKETS:
        print(f"{pk.pid:>4}  {pk.name:<32} {pk.size:>3} {str(pk.signed):<4} "
              f"{','.join(pk.spec):<22} {pk.notes}")


if __name__ == "__main__":
    _dump()
