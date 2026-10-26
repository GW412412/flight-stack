"""Pure software authority gate; deliberately has no MAVLink or actuator output.

This module validates proposals before any future adapter can act on them. It
is not a flight-safety controller and must not be treated as a hardware interlock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from threading import RLock


class AuthorityState(str, Enum):
    """AI authority lifecycle. New instances always start locked."""

    LOCKED = "locked"
    ENABLED = "enabled"
    TAKEOVER_LATCHED = "takeover_latched"
    FAULT_LATCHED = "fault_latched"


class DenialReason(str, Enum):
    LOCKED = "authority_not_enabled"
    STALE_GENERATION = "stale_authority_generation"
    EXPIRED = "request_expired_or_invalid_time"
    DUPLICATE = "duplicate_request_id"
    INVALID_ID = "invalid_request_id"
    NOT_ALLOWED = "command_not_allowlisted"
    INVALID_COMMAND = "invalid_command"


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """A proposal envelope; times use the caller's monotonic clock in seconds."""

    request_id: str
    command: str
    authority_generation: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Validation outcome. A permit is not itself a command transmission."""

    allowed: bool
    reason: str
    authority_generation: int


@dataclass(frozen=True, slots=True)
class GateSnapshot:
    state: AuthorityState
    authority_generation: int


class CommandGate:
    """Fail-closed gate for future command adapters.

    The gate starts locked. Only an explicit pilot action can enable authority;
    takeover and fault transitions invalidate all previously issued generations.
    It intentionally has no method to send commands or activate spray hardware.
    """

    DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
        {"request_status", "propose_target", "propose_bounded_move", "request_land"}
    )

    def __init__(self, allowlist: frozenset[str] | None = None) -> None:
        self._lock = RLock()
        self._state = AuthorityState.LOCKED
        self._generation = 0
        self._seen_request_ids: set[str] = set()
        self._allowlist = frozenset(self.DEFAULT_ALLOWLIST if allowlist is None else allowlist)

    def snapshot(self) -> GateSnapshot:
        with self._lock:
            return GateSnapshot(self._state, self._generation)

    def pilot_enable(self, *, telemetry_fresh: bool, mode_allowed: bool, faults_clear: bool) -> GateSnapshot:
        """Enable only on an explicit pilot action with all prerequisites true."""
        with self._lock:
            if not (telemetry_fresh and mode_allowed and faults_clear):
                self._state = AuthorityState.FAULT_LATCHED
                self._generation += 1
                return GateSnapshot(self._state, self._generation)
            if self._state is AuthorityState.ENABLED:
                return GateSnapshot(self._state, self._generation)
            self._generation += 1
            self._seen_request_ids.clear()
            self._state = AuthorityState.ENABLED
            return GateSnapshot(self._state, self._generation)

    def pilot_takeover(self) -> GateSnapshot:
        """Latch AI authority off and invalidate queued/late proposals."""
        with self._lock:
            self._generation += 1
            self._state = AuthorityState.TAKEOVER_LATCHED
            self._seen_request_ids.clear()
            return GateSnapshot(self._state, self._generation)

    def latch_fault(self) -> GateSnapshot:
        """Fail closed on a watchdog, telemetry, mode or process-health fault."""
        with self._lock:
            self._generation += 1
            self._state = AuthorityState.FAULT_LATCHED
            self._seen_request_ids.clear()
            return GateSnapshot(self._state, self._generation)

    def validate(self, request: CommandRequest, *, now: float) -> GateDecision:
        """Validate a proposal at the point of use; never transmits it."""
        with self._lock:
            def deny(reason: DenialReason) -> GateDecision:
                return GateDecision(False, reason.value, self._generation)

            if self._state is not AuthorityState.ENABLED:
                return deny(DenialReason.LOCKED)
            if request.authority_generation != self._generation:
                return deny(DenialReason.STALE_GENERATION)
            if not request.request_id or not request.request_id.strip():
                return deny(DenialReason.INVALID_ID)
            if request.request_id in self._seen_request_ids:
                return deny(DenialReason.DUPLICATE)
            if (
                isinstance(now, bool)
                or isinstance(request.expires_at, bool)
                or not isinstance(now, (int, float))
                or not isinstance(request.expires_at, (int, float))
                or not isfinite(now)
                or not isfinite(request.expires_at)
            ):
                return deny(DenialReason.EXPIRED)
            if request.expires_at <= now or request.expires_at <= 0:
                return deny(DenialReason.EXPIRED)
            if not isinstance(request.command, str) or not request.command:
                return deny(DenialReason.INVALID_COMMAND)
            if request.command not in self._allowlist:
                return deny(DenialReason.NOT_ALLOWED)
            self._seen_request_ids.add(request.request_id)
            return GateDecision(True, "validated_proposal_only", self._generation)
