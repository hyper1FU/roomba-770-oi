"""Cycle through every plausible BRC wake configuration and see if any of them
elicit a boot banner from the Roomba.

Many third-party Roomba serial cables route the BRC pin either to DTR or to RTS
on the USB-side connector. A few also invert the polarity (DTR-true == TTL-high
instead of TTL-low). Without a logic analyser we can't see this directly, so we
just try every combination:

    line ∈ {DTR, RTS}
    asserted ∈ {True, False}        # which pyserial bool drives the line low
    pulse_ms ∈ {250, 1000}

For each combination we:
    1. open the port
    2. set the *opposite* of the asserted state on both DTR and RTS (idle high)
    3. wait 200 ms (let things settle)
    4. assert the chosen line (TTL low) for pulse_ms
    5. release the line
    6. listen for up to 3 s for any bytes from the Roomba
    7. log the bytes received, ASCII-decoded preview, and bytes-per-second rate.

If ANY combination produces an ASCII banner, you know which line + polarity
your cable uses, and we'll add it to the diagnostic notes.

Also reports the modem-status line states (CTS, DSR, RI, CD) on open. If any
of those toggle when we change DTR/RTS, the cable has an internal loopback
between those control lines.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serial  # noqa: E402

from roomba770.oi import hexlify, printable_preview  # noqa: E402
from scripts._common import append_worklog, capture_path  # noqa: E402


def line_states(s: serial.Serial) -> str:
    return (f"CTS={int(s.cts)} DSR={int(s.dsr)} RI={int(s.ri)} CD={int(s.cd)}")


def try_one(
    port: str, baud: int, line: str, asserted: bool, pulse_ms: int,
    listen_s: float,
) -> dict:
    """Open port, drive BRC, listen, return result dict."""
    result: dict = {
        "line": line, "asserted": asserted, "pulse_ms": pulse_ms,
        "got": b"", "open_status": "", "states_idle": "", "states_pulse": "",
        "states_release": "",
    }
    try:
        s = serial.Serial(
            port=port, baudrate=baud, bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
            timeout=0.3,
        )
    except Exception as exc:
        result["open_status"] = f"open failed: {exc!r}"
        return result
    try:
        # Idle state: both DTR and RTS to the *opposite* of asserted, so the
        # line we choose is in the inactive state before we pulse.
        s.dtr = not asserted
        s.rts = not asserted
        time.sleep(0.2)
        s.reset_input_buffer()
        result["states_idle"] = line_states(s)
        # Pulse the chosen line
        if line == "DTR":
            s.dtr = asserted
        else:
            s.rts = asserted
        result["states_pulse"] = line_states(s)
        time.sleep(pulse_ms / 1000.0)
        # Release
        if line == "DTR":
            s.dtr = not asserted
        else:
            s.rts = not asserted
        result["states_release"] = line_states(s)

        # Listen
        deadline = time.time() + listen_s
        buf = bytearray()
        while time.time() < deadline:
            chunk = s.read(s.in_waiting or 1)
            if chunk:
                buf.extend(chunk)
            else:
                if buf:
                    # got something — give it 200 ms more then stop early
                    time.sleep(0.2)
                    extra = s.read(s.in_waiting or 1)
                    if extra:
                        buf.extend(extra)
                    break
        result["got"] = bytes(buf)
    finally:
        s.close()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--listen-s", type=float, default=3.0)
    args = ap.parse_args()

    out_path = capture_path("probe_brc_lines")
    log: list[str] = []

    def emit(s: str) -> None:
        print(s)
        log.append(s)

    emit(f"# probe_brc_lines on {args.port} @ {args.baud}")

    combos = [
        ("DTR", True,  250),
        ("DTR", True,  1000),
        ("DTR", False, 250),
        ("DTR", False, 1000),
        ("RTS", True,  250),
        ("RTS", True,  1000),
        ("RTS", False, 250),
        ("RTS", False, 1000),
    ]
    any_reply = False
    for line, asserted, pulse_ms in combos:
        emit("")
        emit(f"--- {line} asserted={asserted} pulse={pulse_ms} ms ---")
        r = try_one(args.port, args.baud, line, asserted, pulse_ms,
                    listen_s=args.listen_s)
        if r["open_status"]:
            emit(f"  {r['open_status']}")
            continue
        emit(f"  modem-status  idle:    {r['states_idle']}")
        emit(f"  modem-status  pulsed:  {r['states_pulse']}")
        emit(f"  modem-status  release: {r['states_release']}")
        got = r["got"]
        if got:
            any_reply = True
            emit(f"  RECEIVED {len(got)} bytes!")
            emit(f"    hex     : {hexlify(got[:64])}{' ...' if len(got) > 64 else ''}")
            emit(f"    printable: {printable_preview(got)!r}")
            ascii_text = got.decode("ascii", errors="replace")
            for ln in ascii_text.splitlines():
                if ln.strip():
                    emit(f"    line    : {ln.strip()}")
        else:
            emit("  no bytes received.")

    emit("")
    if any_reply:
        emit("VERDICT: at least one BRC variant elicited a reply — "
             "your cable's BRC line and polarity are now known. "
             "Use the matching combination in subsequent probes.")
    else:
        emit("VERDICT: none of the 8 BRC variants produced any reply from the "
             "Roomba. This strongly suggests the problem is on the data lines "
             "(Rx/Tx swap, GND not connected, or broken wire) rather than the "
             "BRC control. Inspect the Mini-DIN crimping.")

    out_path.write_text("\n".join(log), encoding="utf-8")
    append_worklog(
        f"probe_brc_lines on {args.port}: any_reply={any_reply}, "
        f"file={out_path.name}"
    )
    sys.exit(0 if any_reply else 1)


if __name__ == "__main__":
    main()
