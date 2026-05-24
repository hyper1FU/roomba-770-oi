"""Send Start (128) then immediately Stop (173) and capture every byte the
Roomba prints in the next ``--listen-s`` seconds.

Background: during probe_opcodes we noticed that opcode 173 (Stop) caused the
770 to emit ASCII text containing 'start-charge: 2012-08-22-...'. That looks
like a firmware diagnostic dump. This script captures the entire dump so we
can read the firmware version / build date and any other model fingerprints.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roomba770.oi import Roomba, hexlify, printable_preview  # noqa: E402
from scripts._common import append_worklog, capture_path, wake_brc  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--listen-s", type=float, default=5.0)
    args = ap.parse_args()

    raw_path = capture_path("stop_banner", ext="bin")
    log_path = capture_path("stop_banner", ext="log")

    with Roomba(port=args.port, baud=args.baud, timeout=0.3) as r:
        pre = wake_brc(r)
        if pre:
            print(f"[wake] {len(pre)} bytes during BRC settle: {hexlify(pre[:32])}")

        r.send_opcode(128)  # Start -> Passive
        time.sleep(0.1)
        r.drain_input()
        print(f"Sending Stop (173), then listening {args.listen_s} s ...")
        r.send_opcode(173)

        buf = bytearray()
        deadline = time.time() + args.listen_s
        last_byte_time = time.time()
        while time.time() < deadline:
            chunk = r.read_available(settle_s=0.05)
            if chunk:
                buf.extend(chunk)
                last_byte_time = time.time()
            # If we've had 1 s of silence after some data, stop early.
            if buf and time.time() - last_byte_time > 1.0:
                break

    raw_path.write_bytes(bytes(buf))
    text = bytes(buf).decode("ascii", errors="replace")
    log_path.write_text(text, encoding="utf-8")

    print(f"\nCaptured {len(buf)} bytes -> {raw_path}")
    print("--- printable preview ---")
    print(printable_preview(bytes(buf), max_chars=2000))
    print("\n--- ASCII lines ---")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            print(f"  | {stripped}")

    append_worklog(
        f"capture_stop_banner on {args.port}: {len(buf)} bytes, file={raw_path.name}"
    )


if __name__ == "__main__":
    main()
