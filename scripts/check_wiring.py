"""Check that the USB-serial cable to the Roomba is wired correctly.

Strategy:

1. Loopback sanity check: write a known byte before talking to Roomba.
   If we read back exactly what we wrote, the USB adapter's Rx and Tx are
   tied together (rare, but it would explain "every send echoes"). If we
   read garbage at this stage, baud or framing is wrong.
2. Phase A — assume robot is awake:
     - drain input
     - send Start (128)
     - send Sensors (142) with packet 35 (OI Mode)
     - read 1 byte, expect 0..3
3. Phase B — assume robot is asleep:
     - pulse DTR low for 250 ms (BRC wake pulse, if your cable wires
       DTR to BRC, which most do)
     - listen up to 2 s for the boot banner ASCII text
     - retry the Start + OI-mode query
4. Phase C — baud-rate sweep:
     - if A and B both failed, try 19200 (the only other documented
       Roomba baud) and re-do Phase A.

Reports a clear pass/fail with a likely cause for any failure.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serial  # noqa: E402
from serial.tools import list_ports  # noqa: E402

from roomba770.oi import Roomba, hexlify, printable_preview  # noqa: E402
from scripts._common import append_worklog, capture_path  # noqa: E402


@dataclass
class PhaseResult:
    name: str
    success: bool
    detail: str
    raw: bytes = b""
    banner: bytes = b""


def find_port(port: str) -> bool:
    return any(p.device.lower() == port.lower() for p in list_ports.comports())


def loopback_check(port: str, baud: int) -> PhaseResult:
    """Write a single byte and check whether it comes back echoed."""
    test_byte = b"\xA5"
    try:
        with serial.Serial(port=port, baudrate=baud, timeout=0.3) as s:
            s.reset_input_buffer()
            s.write(test_byte)
            s.flush()
            time.sleep(0.15)
            n = s.in_waiting
            raw = s.read(n) if n else b""
    except Exception as exc:
        return PhaseResult("loopback", False, f"open failed: {exc!r}")
    if not raw:
        return PhaseResult(
            "loopback",
            True,
            "no echo (expected for Roomba — Tx is connected only to the robot, "
            "not back to our Rx).",
            raw,
        )
    if raw == test_byte:
        return PhaseResult(
            "loopback",
            False,
            f"the byte we wrote came straight back ({hexlify(raw)}). "
            "This means your USB adapter's Rx and Tx are tied together "
            "(loopback), so we are not actually hearing the Roomba.",
            raw,
        )
    return PhaseResult(
        "loopback",
        False,
        f"got unexpected bytes {hexlify(raw)} without sending any OI command. "
        "Likely causes: baud mismatch (framing noise), some other device on "
        "the line, or the Roomba was already mid-broadcasting a boot banner.",
        raw,
    )


def query_oi_mode(r: Roomba, timeout_s: float = 1.0) -> bytes:
    r.drain_input()
    r.send_opcode(128)             # Start -> Passive
    time.sleep(0.05)
    r.drain_input()                # discard anything Start may have triggered
    r.send_opcode(142, [35])       # Sensors -> OI Mode
    return r.read_exactly(1, timeout_s=timeout_s)


def phase_awake(port: str, baud: int) -> PhaseResult:
    try:
        with Roomba(port=port, baud=baud, timeout=0.4) as r:
            time.sleep(0.1)
            pre = r.drain_input()
            reply = query_oi_mode(r)
            if len(reply) == 1 and reply[0] in (0, 1, 2, 3):
                return PhaseResult(
                    "awake",
                    True,
                    f"OI mode reply = {reply[0]} ({['off','passive','safe','full'][reply[0]]}). "
                    f"Wiring confirmed (Roomba was already awake).",
                    reply,
                )
            if not reply:
                return PhaseResult("awake", False,
                                   "no reply within 1 s.", reply)
            return PhaseResult(
                "awake", False,
                f"got {len(reply)} bytes but value is unexpected: {hexlify(reply)}",
                reply,
            )
    except Exception as exc:
        return PhaseResult("awake", False, f"exception: {exc!r}")


def phase_wake_via_dtr(port: str, baud: int) -> PhaseResult:
    try:
        with Roomba(port=port, baud=baud, timeout=0.4) as r:
            time.sleep(0.1)
            r.drain_input()
            # DTR=True on pyserial -> RS-232 asserted -> TTL pin LOW.
            # Most Roomba cables tie DTR (or RTS) to BRC. Pulse low 250 ms.
            r.pulse_brc_via_dtr(low_ms=250)
            banner = bytearray()
            deadline = time.time() + 2.0
            while time.time() < deadline:
                chunk = r.read_available(settle_s=0.1)
                if chunk:
                    banner.extend(chunk)
                elif banner:
                    break
            reply = query_oi_mode(r)
            if len(reply) == 1 and reply[0] in (0, 1, 2, 3):
                return PhaseResult(
                    "wake_via_dtr",
                    True,
                    f"OI mode reply = {reply[0]} after BRC pulse. "
                    f"Wiring confirmed; DTR is connected to BRC.",
                    reply,
                    bytes(banner),
                )
            if banner and not reply:
                return PhaseResult(
                    "wake_via_dtr", False,
                    f"received {len(banner)} bytes of banner from Roomba but "
                    f"the OI-mode query got no reply. This is unusual; "
                    f"Roomba Tx -> our Rx is OK, our Tx -> Roomba Rx may be flaky.",
                    reply,
                    bytes(banner),
                )
            return PhaseResult(
                "wake_via_dtr", False,
                f"no reply (banner={len(banner)} B, mode={hexlify(reply)})",
                reply,
                bytes(banner),
            )
    except Exception as exc:
        return PhaseResult("wake_via_dtr", False, f"exception: {exc!r}")


def diagnose(loop: PhaseResult, awake: PhaseResult, wake: PhaseResult,
             fallback: PhaseResult | None) -> str:
    if awake.success:
        return ("PASS: Rx/Tx and ground are correctly wired and the Roomba "
                "was already awake. " + awake.detail)
    if wake.success:
        return ("PASS: Rx/Tx and ground are correctly wired. The Roomba was "
                "asleep; pulsing DTR low (BRC wake) woke it up. " + wake.detail)
    if wake.banner and not wake.success:
        return ("PARTIAL: We received bytes from the Roomba (so Roomba TX -> "
                "USB RX is wired correctly) but it never replied to our "
                "Sensors query. Either USB TX -> Roomba RX is broken, the "
                "TTL level is wrong, or the cable's BRC actually triggers a "
                "reset rather than wake. Look at the banner content: "
                f"{printable_preview(wake.banner)!r}")
    if fallback and fallback.success:
        return ("PASS @ 19200 baud: Rx/Tx wired correctly but the Roomba is "
                "running at 19200, not 115200. Probably some earlier session "
                "ran Baud (129) with a non-default code.")
    if not loop.success and loop.raw == b"\xA5":
        return ("FAIL: Loopback detected — the USB adapter's Rx and Tx are "
                "tied together. Rewire so they go to the Roomba.")
    return (
        "FAIL: no reply from Roomba.\nMost likely causes, in order:\n"
        "  1. Rx and Tx are swapped. The cable's TX should go to Roomba pin 3 "
        "(RxD into Roomba); the cable's RX should go to Roomba pin 4 (TxD from "
        "Roomba). Swap them.\n"
        "  2. No ground. The cable's GND must be tied to Roomba pin 6 or 7.\n"
        "  3. BRC not on a controllable line. We tried pulsing DTR, but your "
        "cable may wire BRC to RTS, or to nothing. Try pulsing manually (tie "
        "BRC to GND briefly) and rerun.\n"
        "  4. TTL voltage mismatch. The Roomba expects 3.3-5 V TTL. If your "
        "adapter is true RS-232 (±12 V) without a level shifter, you may "
        "have damaged the input.\n"
        "  5. Baud rate. Default is 115200; we also tried 19200 as a fallback.\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True, help="e.g. COM11")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--skip-fallback", action="store_true",
                    help="Don't try 19200 baud if 115200 fails.")
    args = ap.parse_args()

    if not find_port(args.port):
        print(f"ERROR: {args.port} is not present in the OS port list. "
              "Is the cable plugged in?")
        sys.exit(2)

    out_path = capture_path("check_wiring")
    log_lines: list[str] = []

    def emit(s: str) -> None:
        print(s)
        log_lines.append(s)

    emit(f"# check_wiring on {args.port} @ {args.baud}")
    emit("")
    loop = loopback_check(args.port, args.baud)
    emit(f"[loopback]   success={loop.success}  raw={hexlify(loop.raw) or '-'}")
    emit(f"             {loop.detail}")

    awake = phase_awake(args.port, args.baud)
    emit(f"[awake-try]  success={awake.success}  raw={hexlify(awake.raw) or '-'}")
    emit(f"             {awake.detail}")

    wake = phase_wake_via_dtr(args.port, args.baud)
    emit(f"[wake+DTR]   success={wake.success}  raw={hexlify(wake.raw) or '-'}  "
         f"banner={len(wake.banner)} B")
    emit(f"             {wake.detail}")
    if wake.banner:
        ascii_lines = wake.banner.decode("ascii", errors="replace").splitlines()
        for ln in ascii_lines:
            stripped = ln.strip()
            if stripped:
                emit(f"               banner| {stripped}")

    fallback: PhaseResult | None = None
    if not (awake.success or wake.success) and not args.skip_fallback:
        emit("")
        emit(f"[fallback]   retrying at 19200 baud ...")
        fallback = phase_awake(args.port, 19200)
        emit(f"             success={fallback.success} "
             f"raw={hexlify(fallback.raw) or '-'}  detail={fallback.detail}")

    emit("")
    verdict = diagnose(loop, awake, wake, fallback)
    emit(verdict)

    out_path.write_text("\n".join(log_lines), encoding="utf-8")
    append_worklog(
        f"check_wiring on {args.port}: awake={awake.success} "
        f"wake_via_dtr={wake.success} banner_bytes={len(wake.banner)} "
        f"file={out_path.name}"
    )

    sys.exit(0 if awake.success or wake.success or (fallback and fallback.success) else 1)


if __name__ == "__main__":
    main()
