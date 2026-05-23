"""Wake the Roomba via BRC and capture the boot banner.

On a 500-series Roomba and Create 2, pulsing BRC low for >=50 ms (and ideally
~250 ms) causes the radio to emit an ASCII banner over TXD that includes the
firmware version. We want to know whether the 770 does the same and what string
it sends. That string is our cheapest fingerprint of which OI flavor it implements.

If your USB-serial adapter wires DTR to BRC (most off-the-shelf Roomba serial
cables do), pass ``--use-dtr``. Otherwise pulse BRC manually with whatever your
hardware uses and just run with ``--no-pulse``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roomba770.oi import Roomba, hexlify, printable_preview  # noqa: E402
from scripts._common import add_serial_args, append_worklog, capture_path  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_serial_args(ap)
    ap.add_argument("--use-dtr", action="store_true",
                    help="Pulse DTR (assumes cable wires DTR to BRC).")
    ap.add_argument("--no-pulse", action="store_true",
                    help="Don't pulse BRC; just listen for whatever comes.")
    ap.add_argument("--listen-s", type=float, default=5.0,
                    help="How long to listen after the pulse (seconds).")
    args = ap.parse_args()

    out_path = capture_path("wake_and_banner")
    with Roomba(port=args.port, baud=args.baud, timeout=args.timeout) as r, \
         out_path.open("wb") as raw:

        print(f"Opened {args.port} @ {args.baud}. Capturing to {out_path}")
        # Drain anything queued before we touch BRC.
        leftover = r.drain_input()
        if leftover:
            print(f"[pre-drain] {len(leftover)} bytes: {hexlify(leftover[:32])}...")
            raw.write(b"# pre-drain\n")
            raw.write(leftover)
            raw.write(b"\n")

        if args.use_dtr and not args.no_pulse:
            print("Pulsing BRC via DTR low for 250 ms...")
            r.pulse_brc_via_dtr(low_ms=250)
            raw.write(b"# DTR-pulse-done\n")
        elif not args.no_pulse:
            print("--use-dtr not given. If your cable doesn't auto-pulse BRC, "
                  "do it manually now. (Or re-run with --no-pulse.)")

        deadline = time.time() + args.listen_s
        buf = bytearray()
        while time.time() < deadline:
            chunk = r.read_available(settle_s=0.05)
            if chunk:
                buf.extend(chunk)
                raw.write(chunk)

        if not buf:
            print("Nothing received during listen window.")
            append_worklog(
                f"wake_and_banner: 0 bytes from {args.port} (baud={args.baud})."
            )
            return

        print(f"Received {len(buf)} bytes.")
        print("Hex preview:")
        print(" ", hexlify(bytes(buf[:64])))
        print("Printable preview:")
        print(" ", printable_preview(bytes(buf)))

        ascii_lines = bytes(buf).decode("ascii", errors="replace").splitlines()
        for ln in ascii_lines:
            stripped = ln.strip()
            if stripped:
                print("  >>", stripped)

        append_worklog(
            f"wake_and_banner: captured {len(buf)} bytes from {args.port}, "
            f"file={out_path.name}"
        )


if __name__ == "__main__":
    main()
