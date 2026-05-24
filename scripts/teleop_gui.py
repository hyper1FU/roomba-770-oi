"""Tkinter arrow-key teleop + PWM Motors panel for Roomba 770.

Keys
----
  Up / Down       forward / backward
  Left / Right    rotate in place (CCW / CW)
  Up+Left etc.    curve
  Space           EMERGENCY STOP — halts wheels AND PWM motors, unticks Enable
  R               reconnect (BRC wake -> Start -> Safe)
  Q / Esc / close stop + quit

This program does not send Clean (135), Spot (134), Max (136), or Motors (138).
The only way to engage the cleaning motors is through the explicit PWM Motors
panel (opcode 144), which is gated by an Enable checkbox that starts OFF and
is forced OFF by emergency_stop and Quit. When Enable is OFF the program sends
(0, 0, 0) to opcode 144 every tick so the motors are positively halted, not
just left alone.

The robot is kept in Safe mode, so the firmware will halt wheel motion on
cliff trip / wheel drop / charger attached.

The watchdog stops the wheels if no arrow key has been seen in the last
~120 ms (covers OS key-repeat gaps on both Windows and X11).
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roomba770.oi import Roomba, hexlify  # noqa: E402


# Tick period: how often we recompute velocity and re-send Drive Direct.
TICK_MS = 50
# How fresh a KeyPress must be to count the key as held. OS key-repeat is
# ~30 Hz, so 120 ms covers four missed events without false-releasing.
KEY_FRESH_S = 0.12
# Telemetry every N ticks (50 ms * N).
TELEMETRY_EVERY_N_TICKS = 20  # 1 s

CHARGING_STATES = {
    0: "not charging", 1: "reconditioning", 2: "full charge",
    3: "trickle", 4: "waiting", 5: "fault",
}


def _read_group_3(r: Roomba, timeout_s: float = 0.25) -> dict | None:
    """Query sensor group 3 (packets 21..26, 10 bytes)."""
    r.drain_input()
    r.send_opcode(142, [3])
    data = r.read_exactly(10, timeout_s=timeout_s)
    if len(data) != 10:
        return None
    cs = data[0]
    volt_mV = (data[1] << 8) | data[2]
    cur_mA, = struct.unpack(">h", data[3:5])
    temp_C, = struct.unpack(">b", data[5:6])
    chg_mAh = (data[6] << 8) | data[7]
    cap_mAh = (data[8] << 8) | data[9]
    return {
        "charging_state": cs,
        "voltage_V": volt_mV / 1000.0,
        "current_mA": cur_mA,
        "temp_C": temp_C,
        "charge_mAh": chg_mAh,
        "capacity_mAh": cap_mAh,
        "battery_pct": (chg_mAh / cap_mAh * 100.0) if cap_mAh else 0.0,
    }


def _read_bumps_and_drops(r: Roomba) -> int | None:
    """Packet 7: bumps & wheel drops, 1 byte bitfield."""
    r.drain_input()
    r.send_opcode(142, [7])
    data = r.read_exactly(1, timeout_s=0.2)
    return data[0] if len(data) == 1 else None


def _describe_bumps(b: int) -> str:
    if b is None:
        return "?"
    parts = []
    if b & 0x01: parts.append("bumpR")
    if b & 0x02: parts.append("bumpL")
    if b & 0x04: parts.append("wheelDropR")
    if b & 0x08: parts.append("wheelDropL")
    return ",".join(parts) if parts else "-"


class TeleopApp:
    def __init__(self, master: tk.Tk, roomba: Roomba,
                 default_fwd: int, default_turn: int) -> None:
        self.master = master
        self.r = roomba
        self.key_last_seen: dict[str, float] = {}
        self.connected = False
        self.tick_n = 0
        self.last_sent_vel: tuple[int, int] = (0, 0)
        self.last_sent_pwm: tuple[int, int, int] = (0, 0, 0)
        self.actual_oi_mode: int | None = None
        self._build_ui(default_fwd, default_turn)
        self._bind_keys()
        self.master.after(50, self._connect_initial)
        self.master.after(TICK_MS, self._tick)

    # --- UI -----------------------------------------------------------

    def _build_ui(self, default_fwd: int, default_turn: int) -> None:
        self.master.title("Roomba 770 teleop")
        self.master.minsize(520, 360)

        root = tk.Frame(self.master, padx=12, pady=12)
        root.pack(fill="both", expand=True)

        # Instructions
        instr = tk.Label(root, justify="left", font=("Consolas", 10), text=(
            "Arrow keys drive the wheels. PWM Motors panel below controls\n"
            "the cleaning motors (vacuum / main brush / side brush) via opcode 144.\n"
            "  Up / Down       forward / backward\n"
            "  Left / Right    rotate CCW / CW in place\n"
            "  Space           EMERGENCY STOP (wheels + PWM Motors)\n"
            "  R               reconnect (BRC wake + Start + Safe)\n"
            "  Q / Esc         quit"
        ))
        instr.pack(anchor="w")

        # Status block
        self.status_var = tk.StringVar(value="status: initializing...")
        tk.Label(root, textvariable=self.status_var,
                 anchor="w", font=("Consolas", 10, "bold")).pack(fill="x", pady=(10, 0))

        self.vel_var = tk.StringVar(value="velocity: R=  0  L=  0  mm/s   keys=[]")
        tk.Label(root, textvariable=self.vel_var,
                 anchor="w", font=("Consolas", 10)).pack(fill="x")

        self.tele_var = tk.StringVar(value="telemetry: --")
        tk.Label(root, textvariable=self.tele_var,
                 anchor="w", font=("Consolas", 10)).pack(fill="x")

        self.bump_var = tk.StringVar(value="bumpers/wheels: --")
        tk.Label(root, textvariable=self.bump_var,
                 anchor="w", font=("Consolas", 10)).pack(fill="x")

        # OI mode selector (Safe vs Full) ---------------------------
        mode_frame = tk.LabelFrame(root, text="OI mode", padx=8, pady=4)
        mode_frame.pack(fill="x", pady=(10, 0))

        self.mode_target_var = tk.IntVar(value=2)   # 2 = Safe, 3 = Full
        tk.Radiobutton(
            mode_frame,
            text="Safe (131) — firmware halts on cliff / wheel-drop / charger",
            variable=self.mode_target_var, value=2,
            command=self._mode_target_changed,
        ).pack(anchor="w")
        tk.Radiobutton(
            mode_frame,
            text="Full (132) — NO safeties; robot will drive off a table",
            variable=self.mode_target_var, value=3,
            command=self._mode_target_changed,
            fg="#b00", font=("Consolas", 10, "bold"),
        ).pack(anchor="w")

        self.actual_mode_var = tk.StringVar(value="actual OI mode: ?")
        tk.Label(mode_frame, textvariable=self.actual_mode_var,
                 font=("Consolas", 10, "bold")).pack(anchor="w", pady=(2, 0))

        # Speed sliders
        sl = tk.LabelFrame(root, text="speeds (mm/s)", padx=8, pady=4)
        sl.pack(fill="x", pady=(10, 0))
        tk.Label(sl, text="forward").grid(row=0, column=0, sticky="w")
        self.fwd_slider = tk.Scale(sl, from_=20, to=500, orient="horizontal", length=320)
        self.fwd_slider.set(default_fwd)
        self.fwd_slider.grid(row=0, column=1, sticky="ew")
        tk.Label(sl, text="turn").grid(row=1, column=0, sticky="w")
        self.turn_slider = tk.Scale(sl, from_=20, to=500, orient="horizontal", length=320)
        self.turn_slider.set(default_turn)
        self.turn_slider.grid(row=1, column=1, sticky="ew")
        sl.columnconfigure(1, weight=1)

        # PWM Motors panel (opcode 144) — disabled by default for safety.
        pwm = tk.LabelFrame(root, text="PWM Motors (opcode 144) — vacuum / brushes",
                            padx=8, pady=4)
        pwm.pack(fill="x", pady=(10, 0))

        self.pwm_enable_var = tk.BooleanVar(value=False)
        enable_row = tk.Frame(pwm)
        enable_row.grid(row=0, column=0, columnspan=7, sticky="w")
        tk.Checkbutton(enable_row, text="Enable", variable=self.pwm_enable_var,
                       command=self._pwm_enable_toggled).pack(side="left")
        tk.Label(enable_row, text="(off forces all PWM to 0 every tick)",
                 fg="#888").pack(side="left", padx=(6, 0))

        self.main_brush_var = tk.IntVar(value=0)
        self.side_brush_var = tk.IntVar(value=0)
        self.vacuum_var     = tk.IntVar(value=0)

        def _add_row(row: int, label: str, var: tk.IntVar,
                     low: int, high: int, presets: list[int]) -> None:
            tk.Label(pwm, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6))
            tk.Scale(pwm, from_=low, to=high, orient="horizontal",
                     variable=var, length=240).grid(row=row, column=1, sticky="ew")
            for col, val in enumerate(presets, start=2):
                tk.Button(pwm, text=str(val), width=4,
                          command=lambda v=val, var=var: var.set(v)
                          ).grid(row=row, column=col, padx=1)

        _add_row(1, "Main brush", self.main_brush_var, -127, 127,
                 [-127, -64, 0, 64, 127])
        _add_row(2, "Side brush", self.side_brush_var, -127, 127,
                 [-127, -64, 0, 64, 127])
        _add_row(3, "Vacuum",     self.vacuum_var,     0,    127,
                 [0, 32, 64, 96, 127])
        pwm.columnconfigure(1, weight=1)

        tk.Button(pwm, text="All cleaning motors OFF",
                  command=self._pwm_all_off, bg="#fcd"
                  ).grid(row=4, column=0, columnspan=7,
                         sticky="ew", pady=(6, 0))

        self.pwm_var = tk.StringVar(value="PWM: disabled  (main=0  side=0  vac=0)")
        tk.Label(root, textvariable=self.pwm_var, anchor="w",
                 font=("Consolas", 10)).pack(fill="x")

        # Buttons
        btn = tk.Frame(root)
        btn.pack(fill="x", pady=(10, 0))
        tk.Button(btn, text="STOP (Space)", width=14,
                  command=self.emergency_stop).pack(side="left")
        tk.Button(btn, text="Reconnect (R)", width=14,
                  command=self._connect).pack(side="left", padx=6)
        tk.Button(btn, text="Quit (Q)", width=10,
                  command=self.quit).pack(side="right")

        # Hint
        tk.Label(root,
                 text="Click in the window first so arrow keys go to the GUI.",
                 fg="#888", font=("Consolas", 9)).pack(anchor="w", pady=(8, 0))

        self.master.protocol("WM_DELETE_WINDOW", self.quit)

    # --- key handling -------------------------------------------------

    def _bind_keys(self) -> None:
        # Arrow keys: use "last seen" tracking; works on both Windows
        # (KeyPress repeats while held) and X11 (KeyRelease/KeyPress pairs).
        for k in ("Up", "Down", "Left", "Right"):
            self.master.bind(f"<KeyPress-{k}>",
                             lambda e, k=k: self._on_arrow_press(k))
            self.master.bind(f"<KeyRelease-{k}>",
                             lambda e, k=k: self._on_arrow_release(k))
        self.master.bind("<space>", lambda e: self.emergency_stop())
        self.master.bind("<Escape>", lambda e: self.quit())
        self.master.bind("q", lambda e: self.quit())
        self.master.bind("Q", lambda e: self.quit())
        self.master.bind("r", lambda e: self._connect())
        self.master.bind("R", lambda e: self._connect())
        # If the window loses focus, drop all held keys (safety).
        self.master.bind("<FocusOut>", lambda e: self._on_focus_out())
        # Take focus.
        self.master.focus_set()

    def _on_arrow_press(self, key: str) -> None:
        self.key_last_seen[key] = time.monotonic()

    def _on_arrow_release(self, key: str) -> None:
        # On X11, KeyRelease + KeyPress fires when held; on Windows, KeyRelease
        # only fires on actual release. The freshness check in _tick handles
        # both — we just *don't* immediately forget the key on release, since
        # X11 will replay a press within ~1 ms.
        self.key_last_seen[key] = time.monotonic() - KEY_FRESH_S + 0.04
        # Set "last seen" 80 ms in the past so a real release expires in 40 ms,
        # but an X11 auto-repeat press refreshes it back to "now" before then.

    def _on_focus_out(self) -> None:
        self.key_last_seen.clear()
        # Don't disable PWM on focus-out: the user may want to leave brushes
        # running while they look at terminal output. The Enable checkbox is
        # the explicit on/off; emergency_stop / Quit / All-Off always halt.

    # --- connect / disconnect ----------------------------------------

    def _connect_initial(self) -> None:
        self._connect()

    def _connect(self) -> None:
        target = self.mode_target_var.get()
        target_op = 131 if target == 2 else 132
        target_name = "Safe" if target == 2 else "Full"
        self.status_var.set(
            f"status: connecting (BRC wake + Start + {target_name})..."
        )
        self.master.update_idletasks()
        try:
            self.r.drain_input()
            self.r.pulse_brc_via_dtr(low_ms=250)
            self.r.drain_input()
            self.r.send_opcode(128)              # Start -> Passive
            time.sleep(0.05)
            self.r.send_opcode(131)              # always pass through Safe
            time.sleep(0.05)
            if target_op == 132:                 # if user wants Full, escalate
                self.r.send_opcode(132)
                time.sleep(0.05)
            mode = self.r.query_sensor(35, 1, timeout_s=0.5)
        except Exception as exc:
            self.status_var.set(f"status: connect failed: {exc!r}")
            self.connected = False
            return
        if len(mode) != 1:
            self.status_var.set("status: connect failed: no OI-mode reply. "
                                "Robot may be on the charger or unplugged.")
            self.connected = False
            return
        self.connected = True
        self._update_actual_mode(mode[0])
        self.status_var.set(f"status: connected. target = {target_name}.")

    # --- main tick ---------------------------------------------------

    def _tick(self) -> None:
        self.master.after(TICK_MS, self._tick)
        if not self.connected:
            return

        now = time.monotonic()
        active = {k for k, t in self.key_last_seen.items()
                  if now - t < KEY_FRESH_S}
        fwd_speed = int(self.fwd_slider.get())
        turn_speed = int(self.turn_slider.get())

        fwd_in = 0
        turn_in = 0
        if "Up"    in active: fwd_in  += fwd_speed
        if "Down"  in active: fwd_in  -= fwd_speed
        if "Left"  in active: turn_in += turn_speed
        if "Right" in active: turn_in -= turn_speed

        # Differential mix. Left arrow = CCW = right wheel faster, left
        # wheel slower (or backward).
        right_v = max(-500, min(500, fwd_in + turn_in))
        left_v  = max(-500, min(500, fwd_in - turn_in))

        if (right_v, left_v) != self.last_sent_vel or self.tick_n % 4 == 0:
            # Always re-send at least every 4 ticks (200 ms) as a heartbeat.
            try:
                self.r.drive_direct(right_v, left_v)
                self.last_sent_vel = (right_v, left_v)
            except Exception as exc:
                self.status_var.set(f"status: drive failed: {exc!r}")
                self.connected = False
                return

        self.vel_var.set(
            f"velocity: R={right_v:+4d}  L={left_v:+4d} mm/s   "
            f"keys={sorted(active) or '[]'}"
        )

        # PWM Motors heartbeat. If disabled, force (0,0,0) once and stop
        # touching the line. If enabled, send the slider values continuously.
        if self.pwm_enable_var.get():
            m = max(-127, min(127, self.main_brush_var.get()))
            s = max(-127, min(127, self.side_brush_var.get()))
            v = max(0,    min(127, self.vacuum_var.get()))
            pwm_cmd = (m, s, v)
            if pwm_cmd != self.last_sent_pwm or self.tick_n % 4 == 0:
                try:
                    self.r.pwm_motors(m, s, v)
                    self.last_sent_pwm = pwm_cmd
                except Exception as exc:
                    self.status_var.set(f"status: PWM send failed: {exc!r}")
                    self.connected = False
                    return
            self.pwm_var.set(
                f"PWM: ENABLED   main={m:+4d}  side={s:+4d}  vac={v:3d}"
            )
        else:
            if self.last_sent_pwm != (0, 0, 0):
                try:
                    self.r.pwm_motors(0, 0, 0)
                    self.last_sent_pwm = (0, 0, 0)
                except Exception:
                    pass
            self.pwm_var.set(
                f"PWM: disabled  (sliders: "
                f"main={self.main_brush_var.get():+4d}  "
                f"side={self.side_brush_var.get():+4d}  "
                f"vac={self.vacuum_var.get():3d})"
            )

        # Periodic telemetry. Skip if currently driving (so we don't
        # interleave 142 with 145 right when responsiveness matters).
        self.tick_n += 1
        if self.tick_n % TELEMETRY_EVERY_N_TICKS == 0 and not active:
            self._poll_telemetry()

    def _poll_telemetry(self) -> None:
        try:
            t = _read_group_3(self.r)
            bumps = _read_bumps_and_drops(self.r)
            mode_reply = self.r.query_sensor(35, 1, timeout_s=0.2)
            if len(mode_reply) == 1:
                self._update_actual_mode(mode_reply[0])
        except Exception as exc:
            self.tele_var.set(f"telemetry: error {exc!r}")
            return
        if t is None:
            self.tele_var.set("telemetry: short reply")
            return
        cs_name = CHARGING_STATES.get(t["charging_state"], "?")
        self.tele_var.set(
            f"telemetry: V={t['voltage_V']:.2f}V  I={t['current_mA']:+5d}mA  "
            f"T={t['temp_C']:+3d}C  batt={t['charge_mAh']}/{t['capacity_mAh']} "
            f"mAh ({t['battery_pct']:.0f}%)  state={cs_name}"
        )
        self.bump_var.set(f"bumpers/wheels: {_describe_bumps(bumps)}")

    # --- OI mode helpers ---------------------------------------------

    def _mode_target_changed(self) -> None:
        """User clicked one of the Safe/Full radio buttons."""
        target = self.mode_target_var.get()
        if not self.connected:
            return
        try:
            if target == 2:
                self.r.send_opcode(131)          # Safe
            elif target == 3:
                # Full requires being in Safe (or Passive) first; the firmware
                # accepts 132 from either, so just send it.
                self.r.send_opcode(132)
            time.sleep(0.05)
            reply = self.r.query_sensor(35, 1, timeout_s=0.3)
            if len(reply) == 1:
                self._update_actual_mode(reply[0])
            self.status_var.set(
                f"status: mode change requested -> "
                f"{'Safe' if target == 2 else 'Full'}"
            )
        except Exception as exc:
            self.status_var.set(f"status: mode change failed: {exc!r}")

    def _update_actual_mode(self, mode_int: int) -> None:
        """Refresh the 'actual OI mode' label and warn on auto-fallback."""
        self.actual_oi_mode = mode_int
        names = {0: "off", 1: "passive", 2: "safe", 3: "full"}
        name = names.get(mode_int, "?")
        warn = ""
        target = self.mode_target_var.get()
        if mode_int == 1 and target in (2, 3):
            # Firmware dropped us back to Passive — typically a safety event.
            warn = "  <-- DROPPED TO PASSIVE (safety event?). Click Safe or Full to re-arm."
        elif mode_int == 0:
            warn = "  <-- OFF (Stop sent, or robot reset). Click Reconnect."
        elif mode_int != target:
            warn = f"  <-- != target ({target})"
        self.actual_mode_var.set(
            f"actual OI mode: {mode_int} ({name}){warn}"
        )

    # --- PWM Motors helpers ------------------------------------------

    def _pwm_enable_toggled(self) -> None:
        # When user unchecks Enable, kill the cleaning motors immediately
        # (don't wait for the next tick).
        if not self.pwm_enable_var.get():
            try:
                self.r.pwm_motors(0, 0, 0)
                self.last_sent_pwm = (0, 0, 0)
            except Exception:
                pass

    def _pwm_all_off(self) -> None:
        self.pwm_enable_var.set(False)
        self.main_brush_var.set(0)
        self.side_brush_var.set(0)
        self.vacuum_var.set(0)
        try:
            self.r.pwm_motors(0, 0, 0)
            self.last_sent_pwm = (0, 0, 0)
        except Exception:
            pass
        self.status_var.set("status: cleaning motors halted (PWM = 0,0,0).")

    # --- actions -----------------------------------------------------

    def emergency_stop(self) -> None:
        self.key_last_seen.clear()
        try:
            self.r.drive_direct(0, 0)
            self.last_sent_vel = (0, 0)
        except Exception:
            pass
        # Also halt cleaning motors and force the Enable checkbox off so
        # the next tick won't restart them.
        self.pwm_enable_var.set(False)
        try:
            self.r.pwm_motors(0, 0, 0)
            self.last_sent_pwm = (0, 0, 0)
        except Exception:
            pass
        self.status_var.set("status: EMERGENCY STOP — wheels halted.")

    def quit(self) -> None:
        # Halt wheels AND cleaning motors before we close the port.
        try:
            self.r.drive_direct(0, 0)
        except Exception:
            pass
        try:
            self.r.pwm_motors(0, 0, 0)
        except Exception:
            pass
        try:
            # Leave robot in Passive so it can sleep / charge normally.
            self.r.send_opcode(128)
        except Exception:
            pass
        try:
            self.r.close()
        except Exception:
            pass
        self.master.destroy()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True, help="e.g. COM11")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--fwd", type=int, default=150,
                    help="Default forward/backward speed in mm/s (slider can override).")
    ap.add_argument("--turn", type=int, default=100,
                    help="Default per-wheel turn-speed differential in mm/s.")
    args = ap.parse_args()

    if not (20 <= args.fwd <= 500) or not (20 <= args.turn <= 500):
        print("--fwd and --turn must be in 20..500 mm/s")
        sys.exit(2)

    r = Roomba(port=args.port, baud=args.baud, timeout=0.3)
    r.open()

    root = tk.Tk()
    app = TeleopApp(root, r, default_fwd=args.fwd, default_turn=args.turn)
    try:
        root.mainloop()
    finally:
        try:
            r.drive_direct(0, 0)
        except Exception:
            pass
        try:
            r.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
