"""Run a deterministic software-only command-gate demonstration.

From the repository root:
    python simulation/dry_run_demo.py

No vehicle connection or hardware output is possible from this demo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Permit direct execution from the repository root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spray_drone.command_gate.gate import CommandGate, CommandRequest
from spray_drone.mavlink_adapter.dry_run import DryRunVehicleAdapter


def main() -> int:
    gate = CommandGate()
    adapter = DryRunVehicleAdapter(gate)

    # Pilot explicitly enables the software gate under simulated healthy inputs.
    enabled = gate.pilot_enable(
        telemetry_fresh=True, mode_allowed=True, faults_clear=True
    )
    queued = CommandRequest(
        request_id="demo-before-takeover",
        command="propose_target",
        authority_generation=enabled.authority_generation,
        expires_at=100.0,
    )
    accepted_before = adapter.submit(queued, now=10.0)

    # A delayed copy of the same generation is presented after pilot takeover.
    gate.pilot_takeover()
    delayed = CommandRequest(
        request_id="demo-delayed-after-takeover",
        command="propose_target",
        authority_generation=enabled.authority_generation,
        expires_at=100.0,
    )
    rejected_after = adapter.submit(delayed, now=11.0)

    report = {
        "mode": "SOFTWARE_DRY_RUN_ONLY",
        "vehicle_link_opened": False,
        "actuator_interface_present": False,
        "events": [
            {
                "request_id": event.request_id,
                "command": event.command,
                "accepted": event.accepted,
                "reason": event.reason,
                "outcome": event.outcome,
            }
            for event in (accepted_before, rejected_after)
        ],
        "final_authority_state": gate.snapshot().state.value,
    }
    print(json.dumps(report, indent=2))
    return 0 if accepted_before.accepted and not rejected_after.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
