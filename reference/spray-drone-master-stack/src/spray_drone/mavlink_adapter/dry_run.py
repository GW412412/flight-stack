"""Software-only vehicle adapter for exercising the gate without MAVLink.

This adapter records simulated outcomes only. It opens no serial, UDP, TCP or
MAVLink connection and exposes no pump, solenoid, GPIO or actuator interface.
"""
from __future__ import annotations

from dataclasses import dataclass

from spray_drone.command_gate.gate import CommandGate, CommandRequest


@dataclass(frozen=True, slots=True)
class DryRunEvent:
    request_id: str
    command: str
    accepted: bool
    reason: str
    outcome: str


class DryRunVehicleAdapter:
    """Validate requests through the gate and record simulated-only outcomes."""

    def __init__(self, gate: CommandGate) -> None:
        self._gate = gate
        self._events: list[DryRunEvent] = []

    @property
    def events(self) -> tuple[DryRunEvent, ...]:
        return tuple(self._events)

    def submit(self, request: CommandRequest, *, now: float) -> DryRunEvent:
        decision = self._gate.validate(request, now=now)
        event = DryRunEvent(
            request_id=request.request_id,
            command=request.command,
            accepted=decision.allowed,
            reason=decision.reason,
            outcome="SIMULATED_ONLY" if decision.allowed else "REJECTED_NO_ACTION",
        )
        self._events.append(event)
        return event
