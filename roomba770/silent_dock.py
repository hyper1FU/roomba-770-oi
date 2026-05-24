"""Silent return-to-dock controller.

Drives the Roomba 770 back to the Home Base using only ``Drive Direct (145)``,
so the vacuum and brushes never run (unlike the firmware's ``Seek Dock (143)``,
which engages the cleaning motors). Reactive state machine; one tick =
read IR + charging state, decide a per-wheel velocity, send it.

Geometry assumptions (cross-checked on the user's 770, 2026-05-24):
- The Home Base emits three superimposable beams. Each IR receiver returns
  ``0xA0 | bits`` where bit 0 = Force Field, bit 2 = Green Buoy, bit 3 =
  Red Buoy.
- The overlap region of Green and Red is directly in front of the dock and
  is the safe approach corridor. Force Field appears only very close to
  the dock structure.

Strategy:
1. **SEARCH** — no IR visible: spin in place (CCW) until anything appears.
2. **GREEN / RED** — only one buoy visible: arc forward toward the side
   the buoy is on (using the left/right receivers to decide direction).
   This eventually brings the robot into the overlap region.
3. **CENTER** — both buoys visible: drive straight forward.
4. **FINAL** — Force Field + both buoys: very close, slow straight forward.
5. **FF_ONLY** — Force Field but no buoy data: also slow straight forward;
   buoy contact should re-appear within a step or two.
6. **DOCKED** — charging state > 0: stop, done.
7. **TIMEOUT** — give up after ``timeout_s`` seconds.

This is intentionally simple. The Roomba's own dock algorithm uses more
state and history; ours just reacts to the latest reading. Should still
home in reliably from any position with line of sight to the dock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import ir_codes

# State strings (also shown in the GUI status line).
STATE_INIT    = "INIT"
STATE_SEARCH  = "SEARCH"
STATE_GREEN   = "GREEN"
STATE_RED     = "RED"
STATE_CENTER  = "CENTER"
STATE_FF_ONLY = "FF_ONLY"
STATE_FINAL   = "FINAL"
STATE_DOCKED  = "DOCKED"
STATE_TIMEOUT = "TIMEOUT"

TERMINAL_STATES = {STATE_DOCKED, STATE_TIMEOUT}


@dataclass
class SilentDockController:
    """Stateful, no I/O. Caller does the serial reads, calls ``step()``,
    and sends the returned wheel velocities via Drive Direct.
    """

    # Velocity tunables (mm/s). Keep them gentle so a bad geometry call
    # doesn't fling the robot somewhere alarming.
    fwd_speed:  int = 120  # base forward speed during normal approach
    arc_diff:   int = 80   # right-left differential when arcing toward a buoy
    spin_speed: int = 100  # one-wheel speed during in-place search rotation
    final_fwd:  int = 50   # slow forward speed in Force Field / final approach

    timeout_s:  float = 60.0  # give up after this long without docking

    state: str = STATE_INIT
    started_at: float = 0.0
    last_ir_at: float = 0.0

    def start(self) -> None:
        self.state = STATE_SEARCH
        self.started_at = time.monotonic()
        self.last_ir_at = self.started_at

    @property
    def active(self) -> bool:
        return self.state not in ({STATE_INIT} | TERMINAL_STATES)

    def step(self, omni: int, left: int, right: int,
             charging_state: int) -> tuple[int, int, str]:
        """One reactive step.

        Args:
            omni:   value of packet 17.
            left:   value of packet 52.
            right:  value of packet 53.
            charging_state: value of packet 21 (0 = not charging).

        Returns:
            (right_wheel_mm_s, left_wheel_mm_s, status_text).
        """
        now = time.monotonic()
        elapsed = now - self.started_at

        # Termination -----------------------------------------------------
        if charging_state > 0:
            self.state = STATE_DOCKED
            return 0, 0, f"DOCKED (charging={charging_state}, {elapsed:.0f}s)"
        if elapsed > self.timeout_s:
            self.state = STATE_TIMEOUT
            return 0, 0, f"TIMEOUT after {elapsed:.0f}s"

        # Decode IR -------------------------------------------------------
        is_hb = ir_codes.is_home_base
        FF, G, R = ir_codes.FORCE_FIELD, ir_codes.GREEN_BUOY, ir_codes.RED_BUOY

        has_ff_omni = bool(is_hb(omni) and (omni & FF))
        has_g = any(is_hb(c) and (c & G) for c in (omni, left, right))
        has_r = any(is_hb(c) and (c & R) for c in (omni, left, right))

        if any((omni, left, right)):
            self.last_ir_at = now

        # Decision tree (most specific first) -----------------------------
        if has_ff_omni and has_g and has_r:
            self.state = STATE_FINAL
            return self.final_fwd, self.final_fwd, \
                   f"FF+G+R: final approach ({elapsed:.0f}s)"

        if has_g and has_r:
            self.state = STATE_CENTER
            return self.fwd_speed, self.fwd_speed, \
                   f"G+R overlap: forward ({elapsed:.0f}s)"

        if has_ff_omni:
            self.state = STATE_FF_ONLY
            return self.final_fwd, self.final_fwd, \
                   f"FF only: slow forward ({elapsed:.0f}s)"

        if has_g:
            self.state = STATE_GREEN
            return self._arc_toward(left, right, G, elapsed, "G")

        if has_r:
            self.state = STATE_RED
            return self._arc_toward(left, right, R, elapsed, "R")

        # Nothing visible — search by rotating in place (CCW).
        self.state = STATE_SEARCH
        since_ir = now - self.last_ir_at
        return self.spin_speed, -self.spin_speed, \
               f"SEARCH (no IR for {since_ir:.0f}s, total {elapsed:.0f}s)"

    # ------------------------------------------------------------------

    def _arc_toward(self, left: int, right: int, bit: int,
                    elapsed: float, label: str) -> tuple[int, int, str]:
        """Arc forward toward whichever side receiver sees the given bit.

        If both sides see it, or neither (only omni), arc to a fixed default
        (left = CCW).
        """
        is_hb = ir_codes.is_home_base
        on_left  = is_hb(left)  and (left  & bit)
        on_right = is_hb(right) and (right & bit)

        if on_right and not on_left:
            # Buoy on robot's right side -> turn nose right
            r_v = self.fwd_speed - self.arc_diff
            l_v = self.fwd_speed
            tag = f"{label} on right: arc right"
        elif on_left and not on_right:
            r_v = self.fwd_speed
            l_v = self.fwd_speed - self.arc_diff
            tag = f"{label} on left: arc left"
        else:
            # Only omni — pick a default direction (arc left / CCW).
            r_v = self.fwd_speed
            l_v = self.fwd_speed - self.arc_diff
            tag = f"{label} on omni only: arc left (default)"
        return r_v, l_v, f"{tag} ({elapsed:.0f}s)"
