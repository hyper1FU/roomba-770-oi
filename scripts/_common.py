"""Shared helpers for the probe scripts."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_DIR = REPO_ROOT / "captures"


def capture_path(probe_name: str, ext: str = "log") -> Path:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return CAPTURE_DIR / f"{stamp}_{probe_name}.{ext}"


def add_serial_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--port",
        required=True,
        help="Serial port the Roomba is on. e.g. COM5 on Windows, /dev/ttyUSB0 on Linux.",
    )
    p.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Baud rate (default 115200; Roomba supports down to 19200 via opcode 129).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=0.5,
        help="Per-read timeout in seconds (default 0.5).",
    )


def wake_brc(roomba, low_ms: int = 250, settle_s: float = 0.4) -> bytes:
    """Pulse DTR low (=BRC low on our cable) to wake the Roomba, then drain
    whatever boot bytes arrive in ``settle_s``. Returns those drained bytes
    so the caller can inspect them for a banner. Safe to call even if the
    robot is already awake."""
    import time
    roomba.drain_input()
    # pulse_brc() dispatches: control port over the pass-through, DTR on a
    # real cable. pulse_brc_via_dtr() would be a no-op over TCP.
    roomba.pulse_brc(low_ms=low_ms)
    time.sleep(settle_s)
    # in_waiting is unusable on socket:// -- see Roomba.read_buffered().
    return roomba.read_buffered()


def append_worklog(line: str) -> None:
    """Append a single line (with newline) to WORKLOG.md."""
    worklog = REPO_ROOT / "WORKLOG.md"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with worklog.open("a", encoding="utf-8") as fh:
        fh.write(f"- {stamp}  {line}\n")
