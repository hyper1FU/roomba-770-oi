"""Minimal Roomba OI wrapper used by the probe scripts.

Intentionally low-level: opens the serial port, exposes raw write + timed read.
Higher-level "drive forward" / "play song" helpers belong in caller code; for
investigation we want to see exactly what bytes go on the wire and what comes
back.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Iterable

import serial


DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_S = 0.5


@dataclass
class Roomba:
    """Talks to a Roomba over a serial port, or over the network.

    ``port`` accepts anything pyserial's ``serial_for_url`` understands::

        Roomba("COM11")                        # USB-serial cable (as before)
        Roomba("socket://192.168.1.50:4000")   # via the ESP32 pass-through

    The pass-through is the ESP32 on the robot bridging TCP to the Roomba's
    UART (see ``firmware/passthru-protocol.md`` in the 2026-bug repo). The
    data port carries raw bytes; a **second** port (data port + 1) takes
    one-line text commands. BRC lives there because the ESP32 drives BRC from
    a GPIO, not from a modem control line.

    Existing scripts keep working -- only the port string changes.
    """

    port: str
    baud: int = DEFAULT_BAUD
    timeout: float = DEFAULT_TIMEOUT_S
    #: Control endpoint of the pass-through as ``"host:port"``. When left None
    #: it is derived from a ``socket://`` port by adding 1, which is what the
    #: protocol document specifies. Ignored for real serial ports.
    ctrl: str | None = None
    _ser: serial.Serial | None = None

    @property
    def is_passthrough(self) -> bool:
        return self.port.startswith(("socket://", "rfc2217://"))

    def _ctrl_endpoint(self) -> tuple[str, int] | None:
        """(host, port) of the control channel, or None if not applicable."""
        if self.ctrl:
            host, _, p = self.ctrl.rpartition(":")
            return host, int(p)
        if not self.port.startswith("socket://"):
            return None
        host, _, p = self.port[len("socket://"):].rpartition(":")
        return host, int(p) + 1

    def open(self) -> None:
        # serial_for_url handles both "COM11" and "socket://host:port".
        # Flow-control kwargs are dropped: they are meaningless for a socket
        # and were already all-false for the real port.
        self._ser = serial.serial_for_url(
            self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=1.0,
        )
        # Nagle batches our small writes and adds ~40 ms; teleop becomes
        # unusable. pyserial makes no promise about it, so disable it here.
        # Private attribute on purpose -- there is no public accessor.
        sock = getattr(self._ser, "_socket", None)
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

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

    #: How long a bulk read waits for the buffer to go quiet. See ``read_buffered``.
    DRAIN_S = 0.02

    def read_buffered(self, max_bytes: int = 4096, drain_s: float | None = None) -> bytes:
        """Read whatever is buffered, on either transport.

        **Do not use ``in_waiting`` for this.** pyserial's ``socket://``
        transport returns **1 whenever any data is pending**, not the byte
        count. ``read(in_waiting)`` therefore fetches *one byte per call* --
        about 20 B/s at the default settle time. The 66 Hz sensor stream is
        ~5 kB/s, so it backs up immediately.

        Measured against the mock ESP32: ``in_waiting == 1`` with 300+ bytes
        buffered, while a raw socket read pulled 1310 bytes in the same second.

        A short read timeout behaves the same on both transports: it returns
        once the buffer is drained, or after ``drain_s``.
        """
        old = self.ser.timeout
        try:
            self.ser.timeout = self.DRAIN_S if drain_s is None else drain_s
            return self.ser.read(max_bytes)
        finally:
            self.ser.timeout = old

    def read_available(self, settle_s: float = 0.1, max_bytes: int = 4096) -> bytes:
        """Wait `settle_s`, then read whatever is in the receive buffer."""
        time.sleep(settle_s)
        return self.read_buffered(max_bytes)

    def drain_input(self) -> bytes:
        """Read and discard any buffered input. Returns what was discarded."""
        return self.read_buffered()

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

    def ctrl_cmd(self, line: str, timeout: float = 2.0) -> str:
        """Send one line to the pass-through control port and return the reply.

        Raises RuntimeError if this Roomba is not reached over the network.
        Commands: ``PING`` / ``BRC <ms>`` / ``STATUS`` / ``RESET`` / ``STOP``.
        """
        ep = self._ctrl_endpoint()
        if ep is None:
            raise RuntimeError(
                "control port is only available over the pass-through; "
                f"port={self.port!r}"
            )
        with socket.create_connection(ep, timeout) as s:
            s.settimeout(timeout)
            s.sendall(line.rstrip("\n").encode() + b"\n")
            reply = b""
            while not reply.endswith(b"\n") and len(reply) < 4096:
                chunk = s.recv(1024)
                if not chunk:
                    break
                reply += chunk
        return reply.decode(errors="replace").strip()

    def pulse_brc(self, low_ms: int = 250) -> None:
        """Pulse BRC low, whichever way this Roomba is connected.

        Over the pass-through the ESP32 owns the BRC pin (a GPIO), so we ask
        it over the control port. On a direct USB-serial cable we fall back to
        wiggling DTR, which is how the off-the-shelf cables wire BRC.

        Prefer this over ``pulse_brc_via_dtr``: it works in both setups.
        """
        if self._ctrl_endpoint() is not None:
            got = self.ctrl_cmd(f"BRC {int(low_ms)}")
            if not got.startswith("OK"):
                raise RuntimeError(f"BRC failed: {got}")
            time.sleep(0.05)
            return
        self.pulse_brc_via_dtr(low_ms)

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

    def seek_dock(self) -> None:
        """Opcode 143 — start autonomous return-to-dock behavior.

        Side effects:
            - OI mode auto-drops to Passive while the firmware drives.
            - Robot will accept no Drive Direct commands until you re-enter
              Safe or Full (which also interrupts the seek).
            - If the robot can't find the dock it will keep searching until
              it gives up, hits something, or you intervene.
        """
        self.send_opcode(143)


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
