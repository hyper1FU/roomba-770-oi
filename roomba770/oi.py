"""Minimal Roomba OI wrapper used by the probe scripts.

Intentionally low-level: opens the serial port, exposes raw write + timed read.
Higher-level "drive forward" / "play song" helpers belong in caller code; for
investigation we want to see exactly what bytes go on the wire and what comes
back.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import serial


DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_S = 0.5


@dataclass
class Roomba:
    port: str
    baud: int = DEFAULT_BAUD
    timeout: float = DEFAULT_TIMEOUT_S
    _ser: serial.Serial | None = None

    def open(self) -> None:
        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=1.0,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
        )

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def __enter__(self) -> "Roomba":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def ser(self) -> serial.Serial:
        if self._ser is None:
            raise RuntimeError("Roomba: serial port not open")
        return self._ser

    # ---- raw IO --------------------------------------------------------

    def write_bytes(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def read_available(self, settle_s: float = 0.1, max_bytes: int = 4096) -> bytes:
        """Wait `settle_s`, then read whatever is in the receive buffer."""
        time.sleep(settle_s)
        n = min(self.ser.in_waiting, max_bytes)
        return self.ser.read(n) if n else b""

    def drain_input(self) -> bytes:
        """Read and discard any buffered input. Returns what was discarded."""
        n = self.ser.in_waiting
        return self.ser.read(n) if n else b""

    def read_exactly(self, n: int, timeout_s: float | None = None) -> bytes:
        """Block until `n` bytes arrive or timeout. Returns whatever it got."""
        if timeout_s is not None:
            old = self.ser.timeout
            self.ser.timeout = timeout_s
            try:
                return self.ser.read(n)
            finally:
                self.ser.timeout = old
        return self.ser.read(n)

    # ---- BRC / wake ---------------------------------------------------

    def pulse_brc_via_dtr(self, low_ms: int = 250) -> None:
        """If your USB-serial adapter wires DTR to BRC, this pulses BRC low.

        Many off-the-shelf Roomba interface cables do exactly that. If your
        cable wires BRC differently (e.g. RTS, or a separate GPIO), pulse it
        in your own code instead and call ``open()`` afterwards.
        """
        # DTR true => RS-232 line low (about 0 V), which is what BRC expects.
        self.ser.dtr = True
        time.sleep(low_ms / 1000.0)
        self.ser.dtr = False
        time.sleep(0.05)

    # ---- opcode helpers ------------------------------------------------

    def send_opcode(self, opcode: int, data: Iterable[int] = ()) -> None:
        payload = bytes([opcode, *data])
        self.write_bytes(payload)

    def start(self) -> None:
        self.send_opcode(128)

    def safe(self) -> None:
        self.send_opcode(131)

    def full(self) -> None:
        self.send_opcode(132)

    def stop_oi(self) -> None:
        self.send_opcode(173)

    def query_sensor(self, packet_id: int, expect_bytes: int,
                     timeout_s: float = 0.5) -> bytes:
        self.drain_input()
        self.send_opcode(142, [packet_id])
        return self.read_exactly(expect_bytes, timeout_s=timeout_s)

    def drive_direct(self, right_mm_s: int, left_mm_s: int) -> None:
        """Drive each wheel independently in signed mm/s. Range -500..500.

        Values outside the range are clamped silently. This is the only
        movement command we ever use — it leaves the vacuum and brushes off.
        """
        rh, rl = _signed16_be(right_mm_s)
        lh, ll = _signed16_be(left_mm_s)
        self.send_opcode(145, [rh, rl, lh, ll])

    def all_motors_off(self) -> None:
        """Halt all wheel motion. Does NOT touch the vacuum/brushes (which we
        also never started, so they stay off)."""
        self.drive_direct(0, 0)

    def pwm_motors(self, main_brush: int, side_brush: int, vacuum: int) -> None:
        """Opcode 144 — variable-speed control of the cleaning system motors.

        Args:
            main_brush: signed PWM in [-127, 127]. Sign reverses direction.
            side_brush: signed PWM in [-127, 127]. Sign reverses direction.
            vacuum:     unsigned PWM in [0, 127]. (Vacuum cannot reverse.)

        Values are clamped silently. Requires Safe or Full mode."""
        m = max(-127, min(127, int(main_brush)))
        s = max(-127, min(127, int(side_brush)))
        v = max(0, min(127, int(vacuum)))
        self.send_opcode(144, [m & 0xFF, s & 0xFF, v & 0xFF])

    def all_cleaning_motors_off(self) -> None:
        """Send opcode 144 with all three PWMs at 0."""
        self.send_opcode(144, [0, 0, 0])


def _signed16_be(v: int) -> tuple[int, int]:
    """Clamp v to [-500, 500] (Drive Direct's range) then return (hi, lo)."""
    v = max(-500, min(500, int(v)))
    if v < 0:
        v += 0x10000
    return (v >> 8) & 0xFF, v & 0xFF


def hexlify(b: bytes, sep: str = " ") -> str:
    return sep.join(f"{x:02X}" for x in b)


def printable_preview(b: bytes, max_chars: int = 200) -> str:
    out = []
    for x in b[:max_chars]:
        out.append(chr(x) if 32 <= x < 127 else ".")
    return "".join(out)
