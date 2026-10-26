"""Unit tests for fail-closed command-gate policy."""

from spray_drone.command_gate.gate import (
    AuthorityState,
    CommandGate,
    CommandRequest,
)


def enable(gate: CommandGate) -> int:
    return gate.pilot_enable(
        telemetry_fresh=True, mode_allowed=True, faults_clear=True
    ).authority_generation


def request(generation: int, request_id: str = "req-1", command: str = "propose_target", expires_at: float = 20.0) -> CommandRequest:
    return CommandRequest(request_id, command, generation, expires_at)


def test_starts_locked_and_rejects_without_pilot_enable() -> None:
    gate = CommandGate()
    snap = gate.snapshot()
    assert snap.state is AuthorityState.LOCKED
    decision = gate.validate(request(snap.authority_generation), now=10.0)
    assert not decision.allowed
    assert decision.reason == "authority_not_enabled"


def test_explicit_enable_allows_allowlisted_proposal_only() -> None:
    gate = CommandGate()
    generation = enable(gate)
    decision = gate.validate(request(generation), now=10.0)
    assert decision.allowed
    assert decision.reason == "validated_proposal_only"


def test_enable_fails_closed_if_any_prerequisite_is_false() -> None:
    gate = CommandGate()
    snap = gate.pilot_enable(telemetry_fresh=False, mode_allowed=True, faults_clear=True)
    assert snap.state is AuthorityState.FAULT_LATCHED
    assert not gate.validate(request(snap.authority_generation), now=10.0).allowed


def test_takeover_invalidates_generation_and_stays_latched() -> None:
    gate = CommandGate()
    old_generation = enable(gate)
    snap = gate.pilot_takeover()
    assert snap.state is AuthorityState.TAKEOVER_LATCHED
    assert snap.authority_generation > old_generation
    assert not gate.validate(request(old_generation), now=10.0).allowed
    # Heartbeats or normal validation calls do not unlock the gate.
    assert gate.snapshot().state is AuthorityState.TAKEOVER_LATCHED
    assert not gate.validate(request(snap.authority_generation), now=10.0).allowed


def test_only_explicit_pilot_enable_can_clear_takeover_latch() -> None:
    gate = CommandGate()
    enable(gate)
    gate.pilot_takeover()
    new_generation = enable(gate)
    assert gate.snapshot().state is AuthorityState.ENABLED
    assert gate.validate(request(new_generation), now=10.0).allowed


def test_fault_latches_and_invalidates_old_generation() -> None:
    gate = CommandGate()
    generation = enable(gate)
    snap = gate.latch_fault()
    assert snap.state is AuthorityState.FAULT_LATCHED
    assert not gate.validate(request(generation), now=10.0).allowed


def test_rejects_expired_requests() -> None:
    gate = CommandGate()
    generation = enable(gate)
    assert gate.validate(request(generation, expires_at=10.0), now=10.0).reason == "request_expired_or_invalid_time"


def test_rejects_replayed_request_id() -> None:
    gate = CommandGate()
    generation = enable(gate)
    assert gate.validate(request(generation), now=10.0).allowed
    replay = gate.validate(request(generation), now=11.0)
    assert not replay.allowed
    assert replay.reason == "duplicate_request_id"


def test_rejects_unlisted_command_including_spray() -> None:
    gate = CommandGate()
    generation = enable(gate)
    decision = gate.validate(request(generation, command="spray_on"), now=10.0)
    assert not decision.allowed
    assert decision.reason == "command_not_allowlisted"


def test_stale_generation_is_rejected_after_reenable() -> None:
    gate = CommandGate()
    old_generation = enable(gate)
    gate.pilot_takeover()
    new_generation = enable(gate)
    assert new_generation > old_generation
    decision = gate.validate(request(old_generation), now=10.0)
    assert not decision.allowed
    assert decision.reason == "stale_authority_generation"


def test_rejects_non_finite_timestamps() -> None:
    import math

    gate = CommandGate()
    generation = enable(gate)
    assert not gate.validate(request(generation, expires_at=math.nan), now=10.0).allowed
    assert not gate.validate(request(generation, request_id="inf", expires_at=math.inf), now=10.0).allowed
    assert not gate.validate(request(generation, request_id="nan-now"), now=math.nan).allowed


def test_explicit_empty_allowlist_allows_nothing() -> None:
    gate = CommandGate(allowlist=frozenset())
    generation = enable(gate)
    decision = gate.validate(request(generation), now=10.0)
    assert not decision.allowed
    assert decision.reason == "command_not_allowlisted"
