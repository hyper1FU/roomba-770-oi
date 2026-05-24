"""Decode the byte values returned by sensor packets 17 (IR Char Omni),
52 (IR Char Left), and 53 (IR Char Right).

The values come from three IR receivers on the Roomba bumper. Each receiver
returns one byte per OI read, identifying whatever IR transmitter it is
currently seeing (or 0 if nothing).

Two categories of transmitter we care about:

1. **Home Base** — emits three superimposable beams:
   - Force Field (close to the dock)
   - Green Buoy  (one side of the dock's centerline)
   - Red Buoy    (the other side)
   The bytes are a bitfield over the base value 0xA0:
       bit 0 (+1) = Force Field
       bit 2 (+4) = Green Buoy
       bit 3 (+8) = Red Buoy
   So 0xA1=FF, 0xA4=G, 0xA5=FF+G, 0xA8=R, 0xA9=FF+R, 0xAC=R+G, 0xAD=FF+R+G.

2. **iRobot IR remote / Virtual Wall** — assorted single-byte commands.

The mapping below follows the Create 2 OI spec. The 770 may use slightly
different codes; cross-check experimentally and update if needed.
"""

from __future__ import annotations

# Home Base bit flags within the lower nibble of 0xAx.
FORCE_FIELD = 1
GREEN_BUOY  = 4
RED_BUOY    = 8

# Named single-byte codes. Anything not here is reported as "unknown".
NAMED_CODES: dict[int, str] = {
    0:   "none",

    # Home base — explicit names for the 7 valid combinations.
    0xA1: "Force Field",
    0xA4: "Green Buoy",
    0xA5: "Force Field + Green",
    0xA8: "Red Buoy",
    0xA9: "Force Field + Red",
    0xAC: "Green + Red (centered)",
    0xAD: "Force Field + Green + Red (at dock)",

    # Virtual Wall accessory
    162: "Virtual Wall (162)",
    242: "Virtual Wall (242)",

    # iRobot IR Remote
    129: "remote: Left",
    130: "remote: Forward",
    131: "remote: Right",
    132: "remote: Spot",
    133: "remote: Max",
    134: "remote: Small",
    135: "remote: Medium",
    136: "remote: Large / Clean",
    137: "remote: Pause",
    138: "remote: Power",
    139: "remote: Arc Fwd Left",
    140: "remote: Arc Fwd Right",
    141: "remote: Drive Stop",

    # Roomba 600+ remote / scheduling remote (best-effort)
    142: "Send All",
    143: "Seek Dock",
}


def describe(code: int) -> str:
    """Human-readable label for a single IR byte."""
    if code in NAMED_CODES:
        return NAMED_CODES[code]
    if (code & 0xF0) == 0xA0:
        # Probably a home-base bitfield we don't have an explicit name for.
        bits = code & 0x0F
        parts = []
        if bits & FORCE_FIELD: parts.append("FF")
        if bits & GREEN_BUOY:  parts.append("Green")
        if bits & RED_BUOY:    parts.append("Red")
        if bits & 2:           parts.append("bit1?")
        label = "+".join(parts) if parts else "none"
        return f"home-base: {label} (raw 0x{code:02X})"
    return f"unknown (0x{code:02X})"


def is_home_base(code: int) -> bool:
    """True if the byte looks like one of the Home Base IR beams."""
    return code != 0 and (code & 0xF0) == 0xA0


def dock_hint(omni: int, left: int, right: int) -> str:
    """One-line interpretation of the three-receiver state.

    Coarse only — for precise navigation rely on the firmware (Seek Dock).
    """
    codes = (omni, left, right)
    if all(c == 0 for c in codes):
        return "no dock visible"
    if any((c & 0x0F) == (FORCE_FIELD | GREEN_BUOY | RED_BUOY)
           and is_home_base(c) for c in codes):
        return "AT DOCK (Force Field + both buoys)"
    if any(c & FORCE_FIELD and is_home_base(c) for c in codes):
        return "very close to dock (Force Field present)"
    # Both buoys visible -> centered in front of dock
    has_g = any(is_home_base(c) and (c & GREEN_BUOY) for c in codes)
    has_r = any(is_home_base(c) and (c & RED_BUOY)   for c in codes)
    if has_g and has_r:
        return "in CENTER beam (Green + Red visible)"
    # Asymmetric — which side?
    # Green Buoy is on one side of the dock centerline, Red on the other.
    # Which receiver picks it up tells us roughly which way to turn:
    if has_g and not has_r:
        # robot is on the Green side of the dock centerline
        if is_home_base(left) and (left & GREEN_BUOY):
            return "Green seen on LEFT receiver — dock is to your right"
        if is_home_base(right) and (right & GREEN_BUOY):
            return "Green seen on RIGHT receiver — dock is to your left"
        return "in Green buoy zone (dock off to one side)"
    if has_r and not has_g:
        if is_home_base(left) and (left & RED_BUOY):
            return "Red seen on LEFT receiver — dock is to your right"
        if is_home_base(right) and (right & RED_BUOY):
            return "Red seen on RIGHT receiver — dock is to your left"
        return "in Red buoy zone (dock off to one side)"
    return "non-dock IR present"
