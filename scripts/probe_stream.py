"""Exercise the Stream / Pause Stream / Resume Stream / Query List commands.

The 500-series spec says:
  - opcode 148 = Stream (start streaming the given packet IDs at 15 Hz)
  - opcode 149 = Pause/Resume Stream (0 pause, 1 resume)
  - Stream frames are: 19, n-data-bytes, [packet_id, packet_data]*, checksum
                       (checksum = (sum of all preceding bytes + checksum) & 0xFF == 0)

The Create 2 spec says:
  - opcode 147 = Stream
  - opcode 148 = Query List (one-shot)

We probe both opcode-148-as-Stream (500) and opcode-147-as-Stream (Create 2) to
see which the 770 firmware implements. Whichever yields valid 15 Hz framing
with a passing checksum wins.

This script logs the raw bytes plus a per-frame breakdown.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roomba770.oi import Roomba, hexlify  # noqa: E402
from scripts._common import add_serial_args, append_worklog, capture_path  # noqa: E402


PACKETS = [7, 35, 22]  # bumps/wheel-drops (1B), OI mode (1B), voltage (2B)
EXPECTED_PER_FRAME = 1 + 1 + (1 + 1) + (1 + 1) + (1 + 2) + 1
# header (19) + n + (id + 1B) + (id + 1B) + (id + 2B) + checksum


def parse_stream_frame(buf: bytes) -> tuple[dict | None, int]:
    """Try to parse one stream frame starting at the front of buf.

    Returns (frame_dict_or_None, bytes_consumed).
    """
    if len(buf) < 2:
        return None, 0
    if buf[0] != 19:
        return None, 1  # resync: drop one byte
    n_data = buf[1]
    total = 2 + n_data + 1
    if len(buf) < total:
        return None, 0
    body = buf[:total]
    checksum_ok = (sum(body) & 0xFF) == 0
    return {
        "n_data": n_data,
        "checksum_ok": checksum_ok,
        "raw": body.hex(),
    }, total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_serial_args(ap)
    ap.add_argument("--stream-opcode", type=int, choices=(147, 148), default=148,
                    help="Which opcode to try as Stream. 500-series=148, Create-2=147.")
    ap.add_argument("--listen-s", type=float, default=3.0,
                    help="How many seconds to capture a stream for.")
    ap.add_argument("--try-query-list", action="store_true",
                    help="Also issue opcode 148 with the same packet list (Create-2 Query List).")
    args = ap.parse_args()

    out_path = capture_path("probe_stream")

    with Roomba(port=args.port, baud=args.baud, timeout=args.timeout) as r, \
         out_path.open("w", encoding="utf-8") as log:

        def emit(line: str) -> None:
            print(line)
            log.write(line + "\n")

        emit(f"# probe_stream on {args.port}, stream-opcode={args.stream_opcode}, "
             f"packets={PACKETS}")
        r.drain_input()
        r.send_opcode(128)  # Start
        time.sleep(0.05)
        r.send_opcode(131)  # Safe
        time.sleep(0.05)

        # Issue stream start ------------------------------------------------
        n = len(PACKETS)
        emit(f"> Send stream opcode {args.stream_opcode}, then {n}, then {PACKETS}")
        r.send_opcode(args.stream_opcode, [n, *PACKETS])

        deadline = time.time() + args.listen_s
        buf = bytearray()
        while time.time() < deadline:
            chunk = r.read_available(settle_s=0.05)
            if chunk:
                buf.extend(chunk)

        emit(f"  captured {len(buf)} bytes in {args.listen_s}s.")
        if not buf:
            emit("  NO STREAM DATA. The 770 may not implement this opcode as Stream.")
        else:
            # Try to parse as 19-framed packets
            frames = 0
            consumed = 0
            view = bytes(buf)
            while consumed < len(view):
                frame, used = parse_stream_frame(view[consumed:])
                if used == 0:
                    break
                if frame is not None:
                    frames += 1
                    if frames <= 4:
                        emit(f"  frame{frames}: n_data={frame['n_data']} "
                             f"checksum_ok={frame['checksum_ok']} raw={frame['raw']}")
                consumed += used
            emit(f"  parsed frames: {frames} (consumed {consumed}/{len(view)} bytes)")

        # Pause / resume ----------------------------------------------------
        emit("> Send Pause Stream (149, 0)")
        r.send_opcode(149, [0])
        time.sleep(0.5)
        leftover = r.drain_input()
        emit(f"  drained {len(leftover)} bytes after pause")

        emit("> Send Resume Stream (149, 1)")
        r.send_opcode(149, [1])
        time.sleep(0.5)
        leftover2 = r.read_available(settle_s=0.3)
        emit(f"  read {len(leftover2)} bytes after resume "
             f"(should be >0 if stream is honored)")
        if leftover2:
            emit(f"  preview: {hexlify(leftover2[:48])}")

        # Final pause to stop the stream cleanly
        r.send_opcode(149, [0])
        time.sleep(0.3)
        r.drain_input()

        # Optional Query List comparison -----------------------------------
        if args.try_query_list:
            emit("> Send Query List on opcode 148 with the same packets")
            r.drain_input()
            r.send_opcode(148, [n, *PACKETS])
            time.sleep(0.4)
            reply = r.read_available(settle_s=0.1)
            emit(f"  reply ({len(reply)}B): {hexlify(reply[:64])}")

        append_worklog(
            f"probe_stream: stream-op={args.stream_opcode}, captured={len(buf)} bytes, "
            f"file={out_path.name}"
        )


if __name__ == "__main__":
    main()
