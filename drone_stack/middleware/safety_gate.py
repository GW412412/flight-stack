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

  Then, at the moment of transmitting (MavlinkHub calls
  `FlightGate.send_if_permitted`):
    7. the authority part (1 and 3) is checked AGAIN, under the same lock the
       pilot-override latch uses, and the single MAVLink write happens before
       that lock is released. So a pilot takeover can never slip in between
       "the gate said yes" and "the packet left".

  Hardening added 2026-10-25 (design ideas credited to Claude, Anthropic,
  claude.ai chat; adapted to this code base - see docs/DISCUSSION.md):
  atomic check-and-send (7), a capped confirmation-code lifetime, the gate's
  own monotonic clock, bounded replay memory for used codes, a bounded audit
  trail (read-only tool `get_gate_audit`), and clean refusal of malformed
  input (wrong types, NaN, infinity) instead of exceptions.

Authority order is unchanged and this module cannot alter it: the pilot's RC
radio wins, then ArduPilot's own failsafes, then this gate. The gate can only
ever ADD refusals.

The flight controller's own failsafes (RC/GCS/battery/EKF/fence) are the
floor underneath all of this and are configured by a person in QGC - this
module never writes failsafe parameters (stack report, Trap 2).
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import secrets
import threading
import time
from collections import OrderedDict, deque
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


# -----------------------------------------------------------------------------
# Input hygiene helpers
# -----------------------------------------------------------------------------
# Everything that reaches the gate ultimately comes from an LLM, an MCP client
# or a JSON document, so it can be the wrong type: a list where a number was
# expected, True instead of 1.0, NaN, infinity, a dict instead of a mode name.
# These helpers turn such input into a plain refusal reason instead of an
# exception. A refusal is always the safe outcome; an exception halfway
# through a check is not (it could skip later checks or crash a tool call).

def finite_number(value: Any, name: str) -> Tuple[Optional[float], Optional[str]]:
    """Return (value as float, None) if `value` is a real, finite number,
    otherwise (None, "plain-English reason").

    Booleans are refused on purpose: in Python True == 1, so `alt_m=True`
    would otherwise quietly become a 1 m takeoff. Strings are refused too,
    even "10": the MCP layer already turns JSON numbers into floats, so a
    string here means something upstream is confused, and we would rather
    stop than guess.
    """
    # numbers.Real also covers numpy scalars from the vision/map code.
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None, f"{name} must be a number (got {type(value).__name__})"
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None, f"{name} must be a number (got {value!r})"
    if not math.isfinite(f):
        return None, f"{name} must be a finite number (got {f})"
    return f, None


def clean_name(value: Any, name: str, max_len: int = 32) -> Tuple[Optional[str], Optional[str]]:
    """Return (UPPER-CASE stripped string, None) for a sensible identifier
    such as a flight-mode or parameter name, otherwise (None, reason)."""
    if not isinstance(value, str):
        return None, f"{name} must be text (got {type(value).__name__})"
    s = value.strip().upper()
    if not s or len(s) > max_len:
        return None, f"{name} must be 1-{max_len} characters (got {value!r})"
    return s, None


# -----------------------------------------------------------------------------
# Confirmation codes
# -----------------------------------------------------------------------------

#: PLACEHOLDER default lifetime of a confirmation code, in seconds.
#: Not measured. Reasoning: in `operator` mode a person has to read the
#: readback, read a 6-character code off the ground-station console and type
#: it into the chat, and a small local LLM then needs a few seconds to make the
#: second tool call. 45 s leaves room for that without leaving an approved
#: "arm" or "take off" lying around while the situation on the field changes.
#: The owner should replace this after timing real operator confirmations.
DEFAULT_CONFIRM_TTL_S = 45.0

#: Hard ceiling for any configured confirm_ttl_s. A profile asking for more
#: than this is refused when the gate is built (the server will not start),
#: so a typo such as 4500 cannot turn a confirmation into a standing permit.
MAX_CONFIRM_TTL_S = 120.0

#: How many confirmation codes may be waiting at once. A model stuck in a loop
#: could otherwise ask for codes forever; past this, the OLDEST pending code is
#: dropped (which can only ever refuse something, never permit it).
MAX_PENDING_CONFIRMATIONS = 16

#: How many used/expired codes we remember (and for at most how long) so that
#: a code is never accepted twice and never re-issued while someone might
#: still type it. Bounded both by count and by age, so memory cannot grow.
REPLAY_MEMORY_MAX = 256
REPLAY_MEMORY_S = 600.0

_TOKEN_CHARS = set("0123456789ABCDEF")
_TOKEN_LEN = 6


def validate_confirm_ttl(ttl_s: Any) -> float:
    """Check a configured confirmation lifetime and return it as a float.

    Raises ValueError (at start-up, never during flight) if it is not a
    finite number in (0, MAX_CONFIRM_TTL_S]."""
    f, why = finite_number(ttl_s, "confirm_ttl_s")
    if why:
        raise ValueError(why)
    if not 0 < f <= MAX_CONFIRM_TTL_S:
        raise ValueError(f"confirm_ttl_s must be > 0 and <= {MAX_CONFIRM_TTL_S:g} s "
                         f"(got {f:g}); a confirmation must not become a standing permit")
    return f


class ConfirmationBook:
    """Two-step read-back / confirm, the same pattern VOICE-CONTROL.md uses.

    Step 1: the tool is called without a token -> `request()` returns a short
            single-use token bound to (action, exact arguments) plus a plain
            English read-back of what will happen.
    Step 2: the tool is called again with `confirm_token=<token>` and the SAME
            arguments -> `consume()` returns True once, then the token is dead.

    Changing any argument (e.g. altitude 10 -> 50) invalidates the token, so a
    model cannot get "arm" confirmed and then slip in something else.

    Hardening (2026-10-25, design ideas credited to Claude, see DISCUSSION.md):

    * Lifetime: every code dies `ttl_s` seconds after it was issued. `ttl_s`
      is checked at start-up against MAX_CONFIRM_TTL_S.
    * Clock: the book reads its own monotonic clock (time.monotonic), which
      never jumps when NTP or a GPS time sync changes the wall clock. Callers
      cannot pass a time in; tests inject a fake clock at construction.
    * Replay memory: used and expired codes are remembered for a while
      (bounded by REPLAY_MEMORY_MAX and REPLAY_MEMORY_S), so a used code is
      refused with a clear reason and is never handed out again.
    * Malformed codes (not text, wrong length, not hex) are refused, never
      raise.
    """

    def __init__(self, ttl_s: float = DEFAULT_CONFIRM_TTL_S,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl_s = validate_confirm_ttl(ttl_s)
        self._clock = clock
        # token -> (action, args-hash, expires_at, readback, issued_at)
        self._pending: Dict[str, Tuple[str, str, float, str, float]] = {}
        # token -> (why it is dead: "used" | "expired" | "dropped", when)
        self._dead: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(action: str, args: Dict[str, Any]) -> str:
        clean = {k: v for k, v in sorted(args.items()) if k != "confirm_token"}
        return hashlib.sha256(f"{action}:{json.dumps(clean, sort_keys=True, default=str)}"
                              .encode()).hexdigest()

    # ---------------------------------------------------------------- issue
    def request(self, action: str, args: Dict[str, Any], readback: str) -> str:
        """Issue a fresh code for (action, args). Never re-uses a code that is
        pending or still in the replay memory."""
        with self._lock:
            self._expire()
            token = secrets.token_hex(3).upper()          # e.g. "4F9A1C"
            while token in self._pending or token in self._dead:
                token = secrets.token_hex(3).upper()
            # Keep the number of open codes bounded: drop the oldest first.
            while len(self._pending) >= MAX_PENDING_CONFIRMATIONS:
                oldest = min(self._pending, key=lambda t: self._pending[t][4])
                self._bury(oldest, "dropped")
            now = self._clock()
            self._pending[token] = (action, self._key(action, args),
                                    now + self.ttl_s, readback, now)
        return token

    # ---------------------------------------------------------------- check
    def check(self, token: Any, action: str, args: Dict[str, Any]) -> Tuple[bool, str]:
        """Try to use `token` for (action, args).

        Returns (True, "ok") exactly once for a valid code, otherwise
        (False, plain-English reason). Never raises. A code given for the
        wrong action or different arguments is NOT burnt (the operator may
        simply have mixed two up), but it is not accepted either."""
        if token is None or token == "":
            return False, "no confirmation code given"
        if not isinstance(token, str):
            return False, f"confirmation code must be text (got {type(token).__name__})"
        tok = token.strip().upper()
        if len(tok) != _TOKEN_LEN or not set(tok) <= _TOKEN_CHARS:
            return False, "confirmation code is malformed (expected 6 hex characters)"
        with self._lock:
            self._expire()
            entry = self._pending.get(tok)
            if entry is None:
                dead = self._dead.get(tok)
                if dead is None:
                    return False, "unknown confirmation code"
                return False, {"used": "confirmation code was already used",
                               "expired": "confirmation code expired "
                                          f"(codes last {self.ttl_s:g} s)",
                               "dropped": "confirmation code was dropped (too many "
                                          "codes waiting); ask for a new one"}[dead[0]]
            p_action, p_key, _exp, _rb, _iss = entry
            if p_action != action or p_key != self._key(action, args):
                return False, "confirmation code is for a different action or arguments"
            self._bury(tok, "used")                       # single use
            return True, "ok"

    def consume(self, token: Optional[str], action: str, args: Dict[str, Any]) -> bool:
        """Backwards-compatible yes/no wrapper around `check()`."""
        return self.check(token, action, args)[0]

    def pending(self) -> List[dict]:
        with self._lock:
            self._expire()
            now = self._clock()
            return [{"token": t, "action": a, "readback": rb,
                     "expires_in_s": round(exp - now, 1)}
                    for t, (a, _k, exp, rb, _i) in self._pending.items()]

    def replay_memory_size(self) -> int:
        """How many dead codes are remembered (for tests / diagnostics)."""
        with self._lock:
            self._expire()
            return len(self._dead)

    # ---------------------------------------------------------------- internals
    def _bury(self, token: str, why: str) -> None:
        """Move a code from pending to the replay memory (lock held)."""
        self._pending.pop(token, None)
        self._dead[token] = (why, self._clock())
        self._dead.move_to_end(token)
        while len(self._dead) > REPLAY_MEMORY_MAX:
            self._dead.popitem(last=False)                # forget the oldest

    def _expire(self) -> None:
        """Expire overdue codes and prune old replay memory (lock held).
        A code is valid up to and including its expiry instant."""
        now = self._clock()
        for t in [t for t, e in self._pending.items() if now > e[2]]:
            self._bury(t, "expired")
        while self._dead:
            t, (_why, when) = next(iter(self._dead.items()))
            if now - when <= REPLAY_MEMORY_S:
                break
            self._dead.popitem(last=False)


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


# -----------------------------------------------------------------------------
# Audit trail
# -----------------------------------------------------------------------------

#: Default number of audit entries kept in memory (oldest dropped first).
DEFAULT_AUDIT_MAX_ENTRIES = 500

# Authority states, in plain words. These describe who may command the
# aircraft through THIS server; the pilot's radio and ArduPilot's own
# failsafes sit above all of them and are never affected by the gate.
STATE_READ_ONLY = "read_only"            # actuation disabled: nothing is sent
STATE_LLM_PERMITTED = "llm_permitted"    # gated LLM actions may be sent
STATE_PILOT_OVERRIDE = "pilot_override"  # pilot took over: only calming actions


class GateAudit:
    """A bounded, in-memory record of what the gate decided and why.

    Each entry is a small dict:
        seq          running number (1, 2, 3 ...) so gaps show dropped entries
        t_mono       the gate's own monotonic clock (what decisions use)
        wall         wall-clock time, for humans reading the log ONLY
        event        e.g. "decision", "send", "override_latched", "confirm"
        action       tool/action name, if any
        allowed      True / False / None (None for pure state changes)
        reasons      list of plain-English reasons (empty when allowed)
        prev_state   authority state before this event
        state        authority state after this event
        cause        what triggered it (mode change text, operator clear ...)

    It never stores confirmation codes, so it is safe to show to the LLM even
    in `operator` confirm mode. It is not persisted: the hub's
    logs/mavlink-commands.jsonl remains the durable record of what was sent.
    """

    def __init__(self, max_entries: int = DEFAULT_AUDIT_MAX_ENTRIES,
                 clock: Callable[[], float] = time.monotonic):
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) \
                or max_entries < 1:
            raise ValueError("audit_max_entries must be a positive whole number")
        self._entries: Deque[dict] = deque(maxlen=max_entries)
        self._clock = clock
        self._seq = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._entries.maxlen or 0

    def record(self, event: str, *, prev_state: str, state: str,
               action: Optional[str] = None, allowed: Optional[bool] = None,
               reasons: Optional[List[str]] = None, cause: Optional[str] = None,
               **extra: Any) -> dict:
        with self._lock:
            self._seq += 1
            entry = {"seq": self._seq, "t_mono": round(self._clock(), 3),
                     "wall": round(time.time(), 3), "event": event,
                     "action": action, "allowed": allowed,
                     "reasons": list(reasons or []), "prev_state": prev_state,
                     "state": state, "cause": cause}
            entry.update(extra)
            self._entries.append(entry)
            return entry

    def entries(self, limit: Optional[int] = None) -> List[dict]:
        """Newest-last copy of the trail (at most `limit` entries)."""
        with self._lock:
            items = [dict(e) for e in self._entries]
        if limit is not None:
            n, why = finite_number(limit, "limit")
            n = 50 if why else int(min(max(n, 1), self.capacity))   # clamp, never raise
            items = items[-n:]
        return items


class GateRefused(Exception):
    """Raised by FlightGate.send_if_permitted when authority was withdrawn
    between the tool's checks and the moment of sending. Carries plain-English
    reasons; DroneTools turns it into a normal {"ok": False, "blocked": True}
    result, so the LLM never sees a traceback."""

    def __init__(self, action: str, reasons: List[str]):
        super().__init__(f"{action}: " + "; ".join(reasons))
        self.action = action
        self.reasons = list(reasons)


class FlightGate:
    """Deterministic checks for LLM flight actions. See module docstring.

    `cfg` is a stack_config.SafetyConfig (duck-typed: any object with the same
    attributes works, which keeps the unit tests free of file I/O).

    Clock: the gate uses its OWN monotonic clock (time.monotonic by default)
    for every time-based decision: confirmation lifetimes, the rate limiter
    and the pilot-override hold time. No method accepts a time from the
    caller. Tests inject a fake clock through the constructor only.

    Authority lock: `_authority_lock` protects the pilot-override latch. The
    hub's reader thread takes it to latch an override; `send_if_permitted`
    holds it across "re-check authority" + "transmit". So an override can land
    either BEFORE the re-check (the send is refused) or AFTER the bytes have
    been handed to the link (the pilot then simply wins on the next command),
    never in between. It is an RLock so the same thread may re-enter.
    """

    def __init__(self, cfg, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self._clock = clock
        ttl = getattr(cfg, "confirm_ttl_s", DEFAULT_CONFIRM_TTL_S)
        self.confirmations = ConfirmationBook(ttl, clock=clock)
        self.rate = RateLimiter(cfg.min_action_interval_s, cfg.max_actions_per_minute,
                                clock=clock)
        self.audit = GateAudit(getattr(cfg, "audit_max_entries", DEFAULT_AUDIT_MAX_ENTRIES),
                               clock=clock)
        self._override: Optional[dict] = None      # pilot-override latch
        self._authority_lock = threading.RLock()

    # ------------------------------------------------------------ authority state
    def authority_state(self) -> str:
        """One word for who may command the aircraft through this server."""
        if not self.cfg.actuation_enabled:
            return STATE_READ_ONLY
        if self.pilot_override():
            return STATE_PILOT_OVERRIDE
        return STATE_LLM_PERMITTED

    # ------------------------------------------------------------ pilot authority
    def note_pilot_override(self, reason: str) -> None:
        """Called by the hub when the mode changed without us asking.

        Takes the authority lock, so if a gated send is in progress this
        waits (a few milliseconds at most: the send is one non-blocking write)
        and the NEXT send is refused."""
        reason = reason if isinstance(reason, str) else repr(reason)
        with self._authority_lock:
            prev = self.authority_state()
            self._override = {"since": self._clock(), "reason": reason}
            self.audit.record("override_latched", prev_state=prev,
                              state=self.authority_state(), cause=reason)

    def clear_pilot_override(self, cause: str = "operator cleared (confirmed)") -> None:
        with self._authority_lock:
            prev = self.authority_state()
            had = self._override is not None
            self._override = None
            if had:
                self.audit.record("override_cleared", prev_state=prev,
                                  state=self.authority_state(), cause=cause)

    def pilot_override(self) -> Optional[dict]:
        """Current latch (or None). Auto-expires if pilot_override_hold_s > 0.

        The returned dict has `since` (gate monotonic clock), `age_s` and
        `reason`."""
        with self._authority_lock:
            o = self._override
            if not o:
                return None
            age = self._clock() - o["since"]
            if self.cfg.pilot_override_hold_s > 0 and age > self.cfg.pilot_override_hold_s:
                self._override = None
                self.audit.record("override_expired", prev_state=STATE_PILOT_OVERRIDE,
                                  state=self.authority_state(),
                                  cause=f"pilot_override_hold_s "
                                        f"({self.cfg.pilot_override_hold_s:g} s) elapsed")
                return None
            return {**o, "age_s": round(age, 1)}

    def rc_inhibit_active(self, telem: dict) -> bool:
        """Radio 'LLM inhibit' switch (optional, cfg.llm_inhibit_rc_channel).

        No RC data yet (no "rc" / no channels / too few channels) counts as
        OFF, exactly as before. RC data that is present but garbled (not a
        dict / list, or a channel value that is not a number) counts as ON:
        fail closed, which is what the old code effectively did by raising."""
        ch = self.cfg.llm_inhibit_rc_channel
        if not ch:
            return False
        if not isinstance(telem, dict):
            return False if telem is None else True
        rc = telem.get("rc")
        if rc is None:
            return False
        if not isinstance(rc, dict):
            return True
        chans = rc.get("channels")
        if chans is None:
            return False
        if not isinstance(chans, (list, tuple)):
            return True
        if len(chans) < ch:
            return False
        pwm, why = finite_number(chans[ch - 1], f"RC{ch}")
        return True if why else pwm > self.cfg.llm_inhibit_pwm_above

    def _authority_reasons(self, telem: Optional[dict]) -> List[str]:
        """The 'who is in charge' part of check_common: master switch, pilot
        override latch and radio inhibit. Re-used at the moment of sending."""
        reasons = []
        if not self.cfg.actuation_enabled:
            reasons.append("actuation disabled (read-only server): set "
                           "safety.actuation_enabled=true or DRONE_ACTUATION=1")
        o = self.pilot_override()
        if o:
            reasons.append(f"pilot override active ({o['reason']}): the pilot has the "
                           "sticks. Only LOITER/BRAKE/LAND/RTL allowed until the "
                           "operator clears it (clear_pilot_override)")
        if self.rc_inhibit_active(telem or {}):
            reasons.append(f"LLM inhibit switch on RC{self.cfg.llm_inhibit_rc_channel} is ON")
        return reasons

    # ------------------------------------------------------------ atomic send
    def send_if_permitted(self, action: Optional[str], send: Callable[[], Any], *,
                          safe: bool = False, telem: Optional[dict] = None) -> Any:
        """Re-check pilot authority and transmit, as ONE step.

        `send` must be short and non-blocking: a single MAVLink write (or a
        no-op in dry-run). Do NOT wait for a COMMAND_ACK inside it, because
        the pilot-override latch has to wait for it.

        * Gated action: under the authority lock, re-check actuation, the
          pilot-override latch and the RC inhibit switch. If any says no,
          raise GateRefused and DO NOT call `send`. Otherwise call `send`
          before releasing the lock.
        * Safe action (LOITER/BRAKE/LAND/RTL/hold): only the master switch is
          re-checked; the lock is not taken, so a calming command is never
          held up by bookkeeping. This keeps the existing rule that safe
          actions bypass the pilot-override latch.

        If `send` itself raises (e.g. serial port gone), the error is
        audited and re-raised; the gate state does not change. Whether that
        should latch a fault is an open question (see DISCUSSION.md).
        """
        action = action if isinstance(action, str) and action else "unlabelled_write"
        if safe:
            if not self.cfg.actuation_enabled:
                reasons = ["actuation disabled (read-only server)"]
                self.audit.record("send", action=action, allowed=False, reasons=reasons,
                                  prev_state=STATE_READ_ONLY, state=STATE_READ_ONLY,
                                  cause="safe action, send-time re-check")
                raise GateRefused(action, reasons)
            return self._audited_send(action, send, safe=True)
        with self._authority_lock:
            reasons = self._authority_reasons(telem)
            if reasons:
                st = self.authority_state()
                self.audit.record("send", action=action, allowed=False, reasons=reasons,
                                  prev_state=st, state=st,
                                  cause="authority withdrawn between check and send")
                raise GateRefused(action, reasons)
            return self._audited_send(action, send, safe=False)

    def _audited_send(self, action: str, send: Callable[[], Any], *, safe: bool) -> Any:
        st = self.authority_state()
        try:
            result = send()
        except Exception as exc:
            self.audit.record("send", action=action, allowed=True, prev_state=st, state=st,
                              reasons=[f"send failed: {type(exc).__name__}: {exc}"],
                              cause="transmit error", safe=safe, sent=False)
            raise
        self.audit.record("send", action=action, allowed=True, prev_state=st, state=st,
                          cause="safe action" if safe else "authority re-checked under lock",
                          safe=safe, sent=True)
        return result

    # ------------------------------------------------------------ audit helpers
    def record_decision(self, action: str, allowed: bool, reasons: List[str], *,
                        stage: str, **extra: Any) -> None:
        """Log one tool-level decision (check passed/refused, confirmation
        asked/refused) in the audit trail. Never raises."""
        try:
            st = self.authority_state()
            self.audit.record("decision", action=action, allowed=allowed,
                              reasons=reasons, prev_state=st, state=st,
                              cause=stage, **extra)
        except Exception:   # the audit must never break a tool call
            pass

    # ------------------------------------------------------------ common checks
    def check_common(self, action: str, status: dict, telem: Optional[dict] = None,
                     *, safe: bool = False) -> Verdict:
        """Checks shared by every action. `safe=True` for calming actions.

        Order is unchanged: actuation -> link -> pilot authority -> rate."""
        status = status if isinstance(status, dict) else {}
        reasons = []
        if not self.cfg.actuation_enabled:
            reasons.append("actuation disabled (read-only server): set "
                           "safety.actuation_enabled=true or DRONE_ACTUATION=1")
        if not status.get("link_ok"):
            reasons.append(f"telemetry link down/stale (heartbeat age "
                           f"{status.get('heartbeat_age_s')})")
        if not safe:
            # actuation was already reported above; add the rest of authority
            reasons += [r for r in self._authority_reasons(telem)
                        if not r.startswith("actuation disabled")]
            r = self.rate.check()
            if r:
                reasons.append(r)
        return Verdict(not reasons, reasons)

    def needs_confirmation(self, action: str) -> bool:
        return action in self.cfg.confirm_actions

    # ------------------------------------------------------------ per-action
    def check_mode(self, mode: str) -> Verdict:
        m, why = clean_name(mode, "mode")
        if why:
            return Verdict(False, [why])
        if m not in self.cfg.allowed_modes:
            return Verdict(False, [f"mode {m} not in allowed_modes {self.cfg.allowed_modes}"])
        return Verdict(True, [])

    def is_safe_mode(self, mode: str) -> bool:
        m, why = clean_name(mode, "mode")
        return why is None and m in self.cfg.safe_modes

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
        alt_m, why = finite_number(alt_m, "takeoff alt_m")
        if why:
            return Verdict(False, [why])
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
        # Type/NaN/inf first: a NaN would make every comparison below False,
        # and a string would raise. Either way: refuse with a plain reason.
        bad = [why for _v, why in (finite_number(lat, f"{label} lat"),
                                   finite_number(lon, f"{label} lon"),
                                   finite_number(alt_m, f"{label} alt_m")) if why]
        if bad:
            return bad
        lat, lon, alt_m = float(lat), float(lon), float(alt_m)
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
        speed_ms, why = finite_number(speed_ms, "speed_ms")
        if why:
            return Verdict(False, [why])
        if not (0.2 <= speed_ms <= self.cfg.max_speed_ms):
            return Verdict(False, [f"speed {speed_ms} m/s outside [0.2, {self.cfg.max_speed_ms}]"])
        return Verdict(True, [])

    def check_param(self, name: str, value: float) -> Verdict:
        n, why = clean_name(name, "parameter name", max_len=16)  # MAVLink limit
        if why:
            return Verdict(False, [why])
        value, why = finite_number(value, f"{n} value")
        if why:
            return Verdict(False, [why])
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
        if not isinstance(waypoints, (list, tuple)):
            return Verdict(False, ["waypoints must be a list of {lat, lon, alt_m}"])
        reasons = []
        if not waypoints:
            reasons.append("empty mission")
        if len(waypoints) > self.cfg.max_mission_items:
            reasons.append(f"{len(waypoints)} items > max {self.cfg.max_mission_items}")
        for i, wp in enumerate(waypoints):
            if not isinstance(wp, dict) or not all(k in wp for k in ("lat", "lon", "alt_m")):
                reasons.append(f"wp{i + 1}: must be an object with lat, lon and alt_m")
                continue
            reasons += self.check_position(wp["lat"], wp["lon"], wp["alt_m"], home,
                                           label=f"wp{i + 1}")
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
            "confirm_ttl_s": self.confirmations.ttl_s,
            "confirm_ttl_max_s": MAX_CONFIRM_TTL_S,
            "rate_limit": {"min_interval_s": c.min_action_interval_s,
                           "per_minute": c.max_actions_per_minute},
            "param_allowlist": {k: list(v) for k, v in c.param_allowlist.items()},
            "pilot_override": self.pilot_override(),
            "authority_state": self.authority_state(),
            "audit_entries": len(self.audit.entries()),
            "llm_inhibit_rc_channel": c.llm_inhibit_rc_channel or None,
        }
