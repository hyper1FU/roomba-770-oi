"""Sweep every documented sensor packet ID. Log which return data and how much.

For each documented packet (individual or group), we:
  1. Drain the input buffer.
  2. Send opcode 142 (Sensors) with the packet ID.
  3. Wait up to ``--per-packet-timeout`` for the documented number of bytes.
  4. Log: got N bytes / expected M / hex / decoded value (for 1- and 2-byte packets).

Also tries packet IDs that are *not* documented (e.g. 59..99, 102..105) so we
can see whether the 770 firmware accepts any "hidden" packets.

Behavioral notes for 500-series spec:
- A Sensors request for an unknown packet ID typically gets no reply at all
  (the robot silently drops the request). If the robot then receives more
  bytes that look like an opcode, it may try to interpret them, so we always
  wait the timeout before sending the next request.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roomba770.oi import Roomba, hexlify  # noqa: E402
from roomba770.opcodes import SENSOR_PACKETS, SENSOR_BY_ID  # noqa: E402
from scripts._common import add_serial_args, append_worklog, capture_path  # noqa: E402


def decode(packet_id: int, raw: bytes) -> str:
    info = SENSOR_BY_ID.get(packet_id)
    if not info or not raw:
        return ""
    if len(raw) != info.size:
        return f"(size mismatch: got {len(raw)}, expected {info.size})"
    if info.size == 1:
        v = raw[0]
        if info.signed and v >= 0x80:
            v -= 0x100
        return f"value={v}"
    if info.size == 2:
        fmt = ">h" if info.signed else ">H"
        (v,) = struct.unpack(fmt, raw)
        return f"value={v}"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_serial_args(ap)
    ap.add_argument(
        "--per-packet-timeout",
        type=float,
        default=0.4,
        help="Seconds to wait for a reply before giving up on a packet ID.",
    )
    ap.add_argument(
        "--probe-undocumented",
        action="store_true",
        help="Also probe packet IDs not in the documented set (59..99, 102..105, "
             "108..127). Most will time out; surprises are interesting.",
    )
    args = ap.parse_args()

    documented_ids = [p.pid for p in SENSOR_PACKETS]
    extra_ids: list[int] = []
    if args.probe_undocumented:
        documented = set(documented_ids)
        for pid in list(range(59, 100)) + list(range(102, 106)) + list(range(108, 128)):
            if pid not in documented:
                extra_ids.append(pid)

    out_path = capture_path("probe_sensors")
    with Roomba(port=args.port, baud=args.baud, timeout=args.timeout) as r, \
         out_path.open("w", encoding="utf-8") as log:

        def emit(line: str) -> None:
            print(line)
            log.write(line + "\n")

        emit(f"# probe_sensors on {args.port} @ {args.baud}")
        emit(f"# documented={len(documented_ids)}  undocumented_extra={len(extra_ids)}")

        r.drain_input()
        emit("> Start (128)")
        r.send_opcode(128)
        time.sleep(0.05)
        emit("> Safe (131)")
        r.send_opcode(131)
        time.sleep(0.05)

        # Documented packets ---------------------------------------------
        for pkt in SENSOR_PACKETS:
            r.drain_input()
            r.send_opcode(142, [pkt.pid])
            t0 = time.perf_counter()
            raw = r.read_exactly(pkt.size, timeout_s=args.per_packet_timeout)
            ms = (time.perf_counter() - t0) * 1000.0
            note = ",".join(pkt.spec)
            emit(
                f"pkt {pkt.pid:>3} {pkt.name:<32} expect={pkt.size}B "
                f"got={len(raw):>2}B  hex={hexlify(raw) or '-':<30} "
                f"{decode(pkt.pid, raw):<24}  spec={note}"
            )

        # Undocumented packets ------------------------------------------
        if extra_ids:
            emit("# ---- undocumented packet sweep ----")
            for pid in extra_ids:
                r.drain_input()
                r.send_opcode(142, [pid])
                # We don't know the expected size; just see what shows up.
                time.sleep(args.per_packet_timeout)
                raw = bytes(r.ser.read(r.ser.in_waiting))
                if raw:
                    emit(f"pkt {pid:>3} UNDOC                              "
                         f"got={len(raw):>2}B  hex={hexlify(raw[:32])}")
                else:
                    emit(f"pkt {pid:>3} UNDOC                              "
                         f"no reply")

        append_worklog(
            f"probe_sensors: {len(documented_ids)} documented + "
            f"{len(extra_ids)} undocumented on {args.port}, file={out_path.name}"
        )


if __name__ == "__main__":
    main()
