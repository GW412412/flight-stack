"""Dry-run adapter tests; these tests never connect to a vehicle."""
from spray_drone.command_gate.gate import CommandGate, CommandRequest
from spray_drone.mavlink_adapter.dry_run import DryRunVehicleAdapter


def test_dry_run_records_accepted_proposal_without_hardware_output() -> None:
    gate = CommandGate()
    generation = gate.pilot_enable(
        telemetry_fresh=True, mode_allowed=True, faults_clear=True
    ).authority_generation
    adapter = DryRunVehicleAdapter(gate)

    event = adapter.submit(
        CommandRequest("one", "propose_target", generation, 20.0), now=10.0
    )

    assert event.accepted
    assert event.outcome == "SIMULATED_ONLY"
    assert len(adapter.events) == 1


def test_dry_run_rejects_delayed_request_after_takeover() -> None:
    gate = CommandGate()
    generation = gate.pilot_enable(
        telemetry_fresh=True, mode_allowed=True, faults_clear=True
    ).authority_generation
    adapter = DryRunVehicleAdapter(gate)
    gate.pilot_takeover()

    event = adapter.submit(
        CommandRequest("late", "propose_target", generation, 20.0), now=10.0
    )

    assert not event.accepted
    assert event.outcome == "REJECTED_NO_ACTION"
    assert event.reason == "authority_not_enabled"


def test_dry_run_has_no_actuator_command_path() -> None:
    gate = CommandGate()
    generation = gate.pilot_enable(
        telemetry_fresh=True, mode_allowed=True, faults_clear=True
    ).authority_generation
    adapter = DryRunVehicleAdapter(gate)

    event = adapter.submit(
        CommandRequest("spray", "spray_on", generation, 20.0), now=10.0
    )

    assert not event.accepted
    assert event.outcome == "REJECTED_NO_ACTION"
