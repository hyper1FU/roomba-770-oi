"""Send each documented opcode in a controlled order and log what happens.

Strategy
--------
The goal is to identify which opcodes the 770 ignores, errors on, or silently
accepts. Because most opcodes have no reply, we infer effect indirectly:

1. After each tested opcode, query packet 35 (OI Mode) and packet 21 (Charging
   State) — these are documented for both 500 and Create 2 and are very likely
   to work on the 770. If they stop replying, the robot probably reset or went
   to sleep.
2. The probe tries opcodes in groups of increasing "danger": mode commands,
   sensor reads, LED/beep, then full motion commands (which we *skip by default*
   unless --allow-motion is passed).
3. For each opcode we log: opcode/name, the bytes sent, any bytes received in
   the next 0.4 s, post-state (OI mode replied? yes/no), and elapsed time.

This probe never sends opcodes 133 (Power) or 144/146 (high-current PWM) by
default. The 600+ specs warn that sending a bad parameter to a motion command
in Full mode can damage the robot.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roomba770.oi import Roomba, hexlify  # noqa: E402
from roomba770.opcodes import OPCODES, OPCODE_BY_NAME  # noqa: E402
from scripts._common import add_serial_args, append_worklog, capture_path  # noqa: E402


SAFE_TO_PROBE: set[int] = {
    # Mode / metadata only — none of these move the robot.
    128, 129, 131, 132, 142, 148, 152, 153, 154,
    155, 156, 157, 158, 162, 163, 164, 165, 167, 168, 173,
    # Pause/resume stream needs Stream first; covered in probe_stream.py
}

# Opcodes deliberately *not* sent by this probe. Use a dedicated test for these.
DANGEROUS: dict[int, str] = {
    133: "Power — would put Roomba to sleep.",
    134: "Spot — starts a cleaning cycle.",
    135: "Clean — starts a cleaning cycle.",
    136: "Max — starts a cleaning cycle.",
    137: "Drive — moves the robot.",
    138: "Motors — engages brush/vacuum motors.",
    139: "LEDs — fine, but covered by a separate test.",
    140: "Song — handled by a separate test.",
    141: "Play — handled by a separate test.",
    143: "Seek Dock — starts docking.",
    144: "PWM Motors — engages brush/vacuum motors.",
    145: "Drive Direct — moves the robot.",
    146: "Drive PWM — moves the robot.",
    147: "Stream — handled by probe_stream.py.",
    149: "Pause/Resume Stream — handled by probe_stream.py.",
}


def parameter_template(opcode: int) -> bytes:
    """Return a minimal-side-effect parameter payload for a given opcode."""
    if opcode == 129:
        return bytes([11])             # 11 = 115200 baud — same as current. No-op.
    if opcode == 142:
        return bytes([35])             # OI mode packet
    if opcode == 148:
        return bytes([1, 35])          # 1 packet ID, packet 35
    if opcode == 152:
        return bytes([1, 153])         # 1-byte script: opcode 153 (Play Script) — no-op
    if opcode == 155:
        return bytes([0])              # wait 0.0 s
    if opcode == 156:
        return bytes([0, 0])
    if opcode == 157:
        return bytes([0, 0])
    if opcode == 158:
        return bytes([0])
    if opcode == 162:
        return bytes([0, 0])           # no LEDs lit
    if opcode == 163:
        return bytes([0, 0, 0, 0])
    if opcode == 164:
        return bytes([0x20, 0x20, 0x20, 0x20])  # four spaces
    if opcode == 165:
        return bytes([0])              # press no buttons
    if opcode == 167:
        return bytes([0] * 15)         # clear schedule
    if opcode == 168:
        # Sun, 00:00 — querying time isn't supported, so this writes a value.
        return bytes([0, 0, 0])
    return b""


def post_mode_check(r: Roomba) -> tuple[int | None, bytes]:
    """Query packet 35 (OI mode). Returns (mode, raw_reply)."""
    raw = r.query_sensor(35, expect_bytes=1, timeout_s=0.4)
    mode = raw[0] if len(raw) == 1 else None
    return mode, raw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_serial_args(ap)
    ap.add_argument(
        "--include",
        nargs="*",
        type=int,
        help="Only probe these opcodes (decimal). Default = curated safe set.",
    )
    ap.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="Also send opcodes flagged as dangerous (movement / cleaning). "
             "ONLY use with the Roomba elevated off the floor and brushes removed.",
    )
    args = ap.parse_args()

    selected: list[int]
    if args.include:
        selected = list(args.include)
    else:
        selected = sorted(SAFE_TO_PROBE)
        if args.allow_dangerous:
            selected.extend(sorted(DANGEROUS))

    out_path = capture_path("probe_opcodes")

    with Roomba(port=args.port, baud=args.baud, timeout=args.timeout) as r, \
         out_path.open("w", encoding="utf-8") as log:

        def emit(line: str) -> None:
            print(line)
            log.write(line + "\n")

        emit(f"# probe_opcodes on {args.port} @ {args.baud}")
        emit(f"# selected opcodes: {selected}")
        r.drain_input()

        # Always Start first.
        emit("> Sending Start (128) ...")
        r.send_opcode(128)
        time.sleep(0.05)
        mode, raw = post_mode_check(r)
        emit(f"  post-Start OI mode = {mode!r} (raw={hexlify(raw)})")
        if mode is None:
            emit("  WARN: no reply to OI-mode query after Start. "
                 "Roomba may be asleep or wiring wrong.")
            append_worklog(
                f"probe_opcodes: no reply after Start on {args.port}, file={out_path.name}"
            )
            return

        # Enter Safe so safety-required opcodes can run without erroring.
        emit("> Sending Safe (131) ...")
        r.send_opcode(131)
        time.sleep(0.05)
        mode, raw = post_mode_check(r)
        emit(f"  post-Safe OI mode = {mode!r} (raw={hexlify(raw)})")

        for op_code in selected:
            op = next((o for o in OPCODES if o.code == op_code), None)
            name = op.name if op else "?"
            spec = ",".join(op.spec) if op else "?"
            note = DANGEROUS.get(op_code, "")
            param = parameter_template(op_code) if op else b""

            if op_code in DANGEROUS and not args.allow_dangerous:
                emit(f"  SKIP {op_code:>3} ({name:<22}) [{spec}] — {note}")
                continue

            emit(f"> Send {op_code:>3} ({name:<22}) [{spec}] params={hexlify(param) or '-'}")
            t0 = time.perf_counter()
            r.drain_input()
            try:
                r.send_opcode(op_code, param)
            except Exception as exc:
                emit(f"  write FAILED: {exc!r}")
                continue
            reply = r.read_available(settle_s=0.15)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            emit(f"  reply ({len(reply)}B): {hexlify(reply[:48])}"
                 f"{' ...' if len(reply) > 48 else ''}  ({elapsed_ms:.0f} ms)")

            # post-check
            mode_after, raw_after = post_mode_check(r)
            emit(f"  post OI mode = {mode_after!r} (raw={hexlify(raw_after)})")
            if mode_after is None:
                emit("  WARN: lost OI-mode reply. Robot may have reset or slept. "
                     "Restarting with Start+Safe.")
                r.send_opcode(128)
                time.sleep(0.05)
                r.send_opcode(131)
                time.sleep(0.05)

        append_worklog(
            f"probe_opcodes: probed {len(selected)} opcodes on {args.port}, "
            f"file={out_path.name}"
        )


if __name__ == "__main__":
    main()
