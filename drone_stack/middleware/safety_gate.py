"""Deterministic safety interlocks. No LLM, no randomness.

Two gates live here:

* `SafetyGate`  - the original spray/arm interlocks (LIVE-PIPELINE +
  INTEGRATION: conf >= 0.50, georeg >= 0.70, RC link present, video trusted,
  fail-closed pump off). Unchanged in spirit; spraying is out of scope for the
  LLM stack and nothing in the MCP server calls `can_spray`.

* `FlightGate`  - the "LLM behind the sticks" gate. Every action tool in
  mcp_server.py calls one `FlightGate.check_*` method BEFORE the hub may
  transmit. It enforces, in this order:
    1. actuation master switch (read-only server unless enabled)
    2. telemetry fresh (no commands into a dead/stale link)
    3. pilot authority: pilot-override latch + optional radio inhibit switch
       (the RC/ELRS pilot always wins; the gate only ever ADDS restrictions)
    4. rate limits
    5. the action's own envelope (alt ceiling, geofence radius from home,
       speed ceiling, mode whitelist, parameter allowlist, mission size)
    6. confirmation token for risky actions (arm, takeoff, set_param, ...)
  "Safe" actions (LOITER/BRAKE/LAND/RTL...) skip 3, 4 and 6: they only ever
  calm the aircraft down, and must never be blocked by bookkeeping.

The flight controller's own failsafes (RC/GCS/battery/EKF/fence) are the
floor underneath all of this and are configured by a person in QGC - this
module never writes failsafe parameters (stack report, Trap 2).
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

CONF_MIN = 0.50
GEOREG_MIN = 0.70


@dataclass
class Verdict:
    allowed: bool
    reasons: list = field(default_factory=list)


def _in_bounds(lat: float, lon: float, zone_bounds: Tuple[float, float, float, float]) -> bool:
    minlat, minlon, maxlat, maxlon = zone_bounds
    return minlat <= lat <= maxlat and minlon <= lon <= maxlon


class SafetyGate:
    def can_arm(self, status: dict) -> Verdict:
        reasons = []
        if "mode" not in status and "armed" not in status:
            reasons.append("no telemetry: link down")
        batt = status.get("battery", 101)
        if batt is not None and 0 <= batt < 30:   # -1 = FC does not know
            reasons.append(f"battery {status.get('battery')}% < 30%")
        fix = status.get("fix_type", 0)
        if fix < 3:
            reasons.append(f"GPS fix type {fix} < 3 (3D)")
        if status.get("mode", "").upper() in ("AUTO", "RTL", "LAND"):
            reasons.append(f"already in {status.get('mode')}")
        if status.get("rc_link_ok") is False:
            reasons.append("RC / ELRS link not present")
        return Verdict(not reasons, reasons)

    def can_spray(
        self,
        status: dict,
        *,
        zone_bounds: Optional[Tuple[float, float, float, float]] = None,
        in_geofence: Optional[bool] = None,
        conf: Optional[float] = None,
        georeg: Optional[float] = None,
        video_trusted: Optional[bool] = None,
        live_id_enabled: Optional[bool] = None,
    ) -> Verdict:
        """Live / pulse spray gate. Fail closed: any unmet check blocks + implies pump off."""
        reasons = []
        if not status.get("armed"):
            reasons.append("aircraft not armed")
        fix = status.get("fix_type", 0)
        if fix < 6:
            reasons.append(f"GPS fix type {fix}: RTK fixed (6) required to spray")
        if status.get("rel_alt", 999) > 30:
            reasons.append(f"altitude {status.get('rel_alt')}m > 30m spray ceiling")
        if status.get("battery", 101) < 20:
            reasons.append(f"battery {status.get('battery')}% < 20% RTL reserve")

        # RC link (ELRS hard authority path must be healthy) — fail closed if unknown
        if status.get("rc_link_ok") is not True:
            reasons.append("RC / ELRS link not present")
        if status.get("heartbeat_age_s") is not None and status.get("heartbeat_age_s") > 2.0:
            reasons.append("telemetry heartbeat stale — fail closed")

        # Video link trusted (live path). Explicit False or missing when required.
        vt = video_trusted if video_trusted is not None else status.get("video_trusted")
        if vt is False:
            reasons.append("video link not trusted — pump must stay off")

        # Live ID operator enable
        lie = live_id_enabled if live_id_enabled is not None else status.get("live_id_enabled")
        if lie is False:
            reasons.append("live ID not operator-enabled")

        # Detection gates when a detection is under review
        if conf is not None and conf < CONF_MIN:
            reasons.append(f"conf {conf} < {CONF_MIN}")
        if georeg is not None and georeg < GEOREG_MIN:
            reasons.append(f"georeg {georeg} < {GEOREG_MIN}")

        # Geofence: prefer explicit zone_bounds + aircraft lat/lon
        if zone_bounds is not None:
            lat, lon = status.get("lat"), status.get("lon")
            if lat is None or lon is None:
                reasons.append("no position for geofence check")
            elif not _in_bounds(float(lat), float(lon), zone_bounds):
                reasons.append(
                    f"position ({lat},{lon}) outside spray zone geofence"
                )
        elif in_geofence is False:
            reasons.append("position outside spray zone geofence")
        elif in_geofence is None and zone_bounds is None:
            # Do not default-true: require caller to supply fence or explicit True
            if status.get("in_geofence") is True:
                pass
            elif status.get("in_geofence") is False:
                reasons.append("position outside spray zone geofence")
            else:
                reasons.append("geofence not verified (pass zone_bounds or in_geofence)")

        # Any fault flag on status
        if status.get("failsafe") or status.get("pump_fault"):
            reasons.append("failsafe or pump fault — fail closed, pump off")

        return Verdict(not reasons, reasons)

    def pump_must_be_off(self, status: dict, **kwargs) -> Verdict:
        """Fail-closed helper: if can_spray is denied, pump must be off."""
        v = self.can_spray(status, **kwargs)
        if not v.allowed:
            return Verdict(True, ["pump off: " + "; ".join(v.reasons)])
        return Verdict(False, ["spray conditions currently allow pump under hub arbiter"])

    def check_mission(self, waypoints: list, zone_bounds: tuple) -> Verdict:
        """waypoints: (lat, lon, alt) list; zone_bounds: (minlat, minlon, maxlat, maxlon)."""
        reasons = []
        minlat, minlon, maxlat, maxlon = zone_bounds
        for i, (lat, lon, alt) in enumerate(waypoints):
            if not (minlat <= lat <= maxlat and minlon <= lon <= maxlon):
                reasons.append(f"wp{i}: ({lat:.5f},{lon:.5f}) outside zone")
            if alt > 50:
                reasons.append(f"wp{i}: alt {alt}m > 50m hard ceiling")
        if len(waypoints) > 500:
            reasons.append("mission too long (>500 items)")
        return Verdict(not reasons, reasons)


# =============================================================================
# FlightGate: guards every LLM-originated flight action
# =============================================================================

def _dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres (duplicated from mav_node on purpose so
    this module has zero imports from the I/O layer and stays unit-testable)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class ConfirmationBook:
    """Two-step read-back / confirm, the same pattern VOICE-CONTROL.md uses.

    Step 1: the tool is called without a token -> `request()` returns a short
            single-use token bound to (action, exact arguments) plus a plain
            English read-back of what will happen.
    Step 2: the tool is called again with `confirm_token=<token>` and the SAME
            arguments -> `consume()` returns True once, then the token is dead.

    Changing any argument (e.g. altitude 10 -> 50) invalidates the token, so a
    model cannot get "arm" confirmed and then slip in something else.
    """

    def __init__(self, ttl_s: float = 60.0, clock: Callable[[], float] = time.time):
        self.ttl_s = ttl_s
        self._clock = clock
        self._pending: Dict[str, Tuple[str, str, float, str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(action: str, args: Dict[str, Any]) -> str:
        clean = {k: v for k, v in sorted(args.items()) if k != "confirm_token"}
        return hashlib.sha256(f"{action}:{json.dumps(clean, sort_keys=True, default=str)}"
                              .encode()).hexdigest()

    def request(self, action: str, args: Dict[str, Any], readback: str) -> str:
        token = secrets.token_hex(3).upper()          # e.g. "4F9A1C"
        with self._lock:
            self._expire()
            self._pending[token] = (action, self._key(action, args),
                                    self._clock() + self.ttl_s, readback)
        return token

    def consume(self, token: Optional[str], action: str, args: Dict[str, Any]) -> bool:
        if not token:
            return False
        token = str(token).strip().upper()
        with self._lock:
            self._expire()
            entry = self._pending.get(token)
            if not entry:
                return False
            p_action, p_key, _exp, _rb = entry
            if p_action != action or p_key != self._key(action, args):
                return False
            del self._pending[token]                  # single use
            return True

    def pending(self) -> List[dict]:
        with self._lock:
            self._expire()
            now = self._clock()
            return [{"token": t, "action": a, "readback": rb,
                     "expires_in_s": round(exp - now, 1)}
                    for t, (a, _k, exp, rb) in self._pending.items()]

    def _expire(self):
        now = self._clock()
        for t in [t for t, e in self._pending.items() if e[2] < now]:
            del self._pending[t]


class RateLimiter:
    """Minimum spacing between actions + a rolling per-minute cap."""

    def __init__(self, min_interval_s: float, per_minute: int,
                 clock: Callable[[], float] = time.monotonic):
        self.min_interval_s = min_interval_s
        self.per_minute = per_minute
        self._clock = clock
        self._times: Deque[float] = deque()
        self._lock = threading.Lock()

    def check(self) -> Optional[str]:
        now = self._clock()
        with self._lock:
            while self._times and self._times[0] < now - 60:
                self._times.popleft()
            if self._times and now - self._times[-1] < self.min_interval_s:
                return (f"rate limit: wait {self.min_interval_s - (now - self._times[-1]):.1f}s "
                        f"between actions")
            if len(self._times) >= self.per_minute:
                return f"rate limit: {self.per_minute} actions/minute reached"
        return None

    def record(self) -> None:
        with self._lock:
            self._times.append(self._clock())


class FlightGate:
    """Deterministic checks for LLM flight actions. See module docstring.

    `cfg` is a stack_config.SafetyConfig (duck-typed: any object with the same
    attributes works, which keeps the unit tests free of file I/O).
    """

    def __init__(self, cfg, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self._clock = clock
        self.confirmations = ConfirmationBook(cfg.confirm_ttl_s, clock=clock)
        self.rate = RateLimiter(cfg.min_action_interval_s, cfg.max_actions_per_minute)
        self._override: Optional[dict] = None      # pilot-override latch

    # ------------------------------------------------------------ pilot authority
    def note_pilot_override(self, reason: str) -> None:
        """Called by the hub when the mode changed without us asking."""
        self._override = {"since": self._clock(), "reason": reason}

    def clear_pilot_override(self) -> None:
        self._override = None

    def pilot_override(self) -> Optional[dict]:
        """Current latch (or None). Auto-expires if pilot_override_hold_s > 0."""
        o = self._override
        if o and self.cfg.pilot_override_hold_s > 0 and \
                self._clock() - o["since"] > self.cfg.pilot_override_hold_s:
            self._override = None
            return None
        return o

    def rc_inhibit_active(self, telem: dict) -> bool:
        """Radio 'LLM inhibit' switch (optional, cfg.llm_inhibit_rc_channel)."""
        ch = self.cfg.llm_inhibit_rc_channel
        if not ch:
            return False
        chans = (telem.get("rc") or {}).get("channels") or []
        return len(chans) >= ch and chans[ch - 1] > self.cfg.llm_inhibit_pwm_above

    # ------------------------------------------------------------ common checks
    def check_common(self, action: str, status: dict, telem: Optional[dict] = None,
                     *, safe: bool = False) -> Verdict:
        """Checks shared by every action. `safe=True` for calming actions."""
        reasons = []
        if not self.cfg.actuation_enabled:
            reasons.append("actuation disabled (read-only server): set "
                           "safety.actuation_enabled=true or DRONE_ACTUATION=1")
        if not status.get("link_ok"):
            reasons.append(f"telemetry link down/stale (heartbeat age "
                           f"{status.get('heartbeat_age_s')})")
        if not safe:
            o = self.pilot_override()
            if o:
                reasons.append(f"pilot override active ({o['reason']}): the pilot has the "
                               "sticks. Only LOITER/BRAKE/LAND/RTL allowed until the "
                               "operator clears it (clear_pilot_override)")
            if self.rc_inhibit_active(telem or {}):
                reasons.append(f"LLM inhibit switch on RC{self.cfg.llm_inhibit_rc_channel} is ON")
            r = self.rate.check()
            if r:
                reasons.append(r)
        return Verdict(not reasons, reasons)

    def needs_confirmation(self, action: str) -> bool:
        return action in self.cfg.confirm_actions

    # ------------------------------------------------------------ per-action
    def check_mode(self, mode: str) -> Verdict:
        m = mode.upper()
        if m not in self.cfg.allowed_modes:
            return Verdict(False, [f"mode {m} not in allowed_modes {self.cfg.allowed_modes}"])
        return Verdict(True, [])

    def is_safe_mode(self, mode: str) -> bool:
        return mode.upper() in self.cfg.safe_modes

    def check_arm(self, status: dict, telem: dict) -> Verdict:
        reasons = []
        if status.get("armed"):
            reasons.append("already armed")
        fix = status.get("fix_type") or 0
        if fix < self.cfg.require_gps_fix:
            reasons.append(f"GPS fix {fix} < required {self.cfg.require_gps_fix}")
        batt = status.get("battery")
        if batt is not None and 0 <= batt < self.cfg.min_battery_pct:
            reasons.append(f"battery {batt}% < {self.cfg.min_battery_pct}%")
        if self.cfg.require_rc_link and status.get("rc_link_ok") is not True:
            reasons.append("RC/ELRS link not confirmed - the pilot must have the radio up "
                           f"to arm (rc_link_ok={status.get('rc_link_ok')})")
        ekf = (telem or {}).get("ekf")
        if ekf is not None and not ekf.get("position_ok"):
            reasons.append(f"EKF position not ok ({ekf.get('flag_names')})")
        if (status.get("mode") or "").upper() not in ("GUIDED", "LOITER", "POSHOLD",
                                                      "ALT_HOLD", "STABILIZE"):
            reasons.append(f"arm from mode {status.get('mode')} not allowed "
                           "(use GUIDED/LOITER)")
        return Verdict(not reasons, reasons)

    def check_disarm(self, status: dict) -> Verdict:
        reasons = []
        if not status.get("armed"):
            reasons.append("already disarmed")
        alt = status.get("rel_alt")
        if alt is not None and alt > 1.0:
            reasons.append(f"airborne (rel_alt {alt:.1f} m): disarm would drop the "
                           "aircraft - use land instead")
        return Verdict(not reasons, reasons)

    def check_takeoff(self, alt_m: float, status: dict) -> Verdict:
        reasons = []
        if not status.get("armed"):
            reasons.append("not armed")
        if (status.get("mode") or "").upper() != "GUIDED":
            reasons.append(f"takeoff needs GUIDED (now {status.get('mode')})")
        if not (self.cfg.min_goto_alt_m <= alt_m <= self.cfg.max_alt_m):
            reasons.append(f"takeoff alt {alt_m} m outside "
                           f"[{self.cfg.min_goto_alt_m}, {self.cfg.max_alt_m}] m")
        return Verdict(not reasons, reasons)

    def check_position(self, lat: float, lon: float, alt_m: float,
                       home: Optional[dict], label: str = "target") -> List[str]:
        """Envelope check for one point: geofence radius from home + altitude."""
        reasons = []
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return [f"{label}: invalid lat/lon ({lat}, {lon})"]
        if home is None:
            reasons.append("home position unknown - cannot verify geofence")
        else:
            d = _dist_m(home["lat"], home["lon"], lat, lon)
            if d > self.cfg.geofence_radius_m:
                reasons.append(f"{label} is {d:.0f} m from home > geofence "
                               f"{self.cfg.geofence_radius_m:.0f} m")
        if not (self.cfg.min_goto_alt_m <= alt_m <= self.cfg.max_alt_m):
            reasons.append(f"{label} alt {alt_m} m outside "
                           f"[{self.cfg.min_goto_alt_m}, {self.cfg.max_alt_m}] m")
        return reasons

    def check_goto(self, lat: float, lon: float, alt_m: float, status: dict,
                   home: Optional[dict]) -> Verdict:
        reasons = self.check_position(lat, lon, alt_m, home)
        if not status.get("armed"):
            reasons.append("not armed")
        if (status.get("mode") or "").upper() != "GUIDED":
            reasons.append(f"goto needs GUIDED (now {status.get('mode')})")
        return Verdict(not reasons, reasons)

    def check_speed(self, speed_ms: float) -> Verdict:
        if not (0.2 <= speed_ms <= self.cfg.max_speed_ms):
            return Verdict(False, [f"speed {speed_ms} m/s outside [0.2, {self.cfg.max_speed_ms}]"])
        return Verdict(True, [])

    def check_param(self, name: str, value: float) -> Verdict:
        n = name.upper()
        lim = self.cfg.param_allowlist.get(n)
        if lim is None:
            return Verdict(False, [f"parameter {n} is not in the LLM allowlist "
                                   f"({sorted(self.cfg.param_allowlist)}); failsafe, "
                                   "fence, arming and hardware params are set by a person in QGC"])
        lo, hi = lim
        if not (lo <= float(value) <= hi):
            return Verdict(False, [f"{n}={value} outside allowed range [{lo}, {hi}]"])
        return Verdict(True, [])

    def check_mission(self, waypoints: List[dict], home: Optional[dict]) -> Verdict:
        """waypoints: [{'lat','lon','alt_m'}...] (simple nav waypoints only)."""
        reasons = []
        if not waypoints:
            reasons.append("empty mission")
        if len(waypoints) > self.cfg.max_mission_items:
            reasons.append(f"{len(waypoints)} items > max {self.cfg.max_mission_items}")
        for i, wp in enumerate(waypoints):
            reasons += self.check_position(float(wp["lat"]), float(wp["lon"]),
                                           float(wp["alt_m"]), home, label=f"wp{i + 1}")
        # de-duplicate the 'home unknown' message
        seen, uniq = set(), []
        for r in reasons:
            if r not in seen:
                seen.add(r)
                uniq.append(r)
        return Verdict(not uniq, uniq)

    def check_start_mission(self, status: dict, mission_count: Optional[int]) -> Verdict:
        reasons = []
        if not status.get("armed"):
            reasons.append("not armed (arm + takeoff in GUIDED first)")
        if not mission_count:
            reasons.append("no mission on the vehicle (upload_mission first)")
        alt = status.get("rel_alt")
        if alt is not None and alt < self.cfg.min_goto_alt_m:
            reasons.append(f"rel_alt {alt:.1f} m < {self.cfg.min_goto_alt_m} m: take off first")
        return Verdict(not reasons, reasons)

    def describe(self) -> dict:
        """Everything the LLM is allowed to know about its limits."""
        c = self.cfg
        return {
            "actuation_enabled": c.actuation_enabled, "dry_run": c.dry_run,
            "max_alt_m": c.max_alt_m, "min_goto_alt_m": c.min_goto_alt_m,
            "geofence_radius_m_from_home": c.geofence_radius_m,
            "max_speed_ms": c.max_speed_ms, "max_mission_items": c.max_mission_items,
            "allowed_modes": c.allowed_modes, "safe_modes_always_allowed": c.safe_modes,
            "confirm_actions": c.confirm_actions, "confirm_mode": c.confirm_mode,
            "rate_limit": {"min_interval_s": c.min_action_interval_s,
                           "per_minute": c.max_actions_per_minute},
            "param_allowlist": {k: list(v) for k, v in c.param_allowlist.items()},
            "pilot_override": self.pilot_override(),
            "llm_inhibit_rc_channel": c.llm_inhibit_rc_channel or None,
        }
