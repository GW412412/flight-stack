# Safety case and hazards

**Status:** Initial hazard register, not a safety certification or permission to fly.

| Hazard | Required controls | Evidence still required |
|---|---|---|
| AI acts after pilot takeover | Latched gateway authority, queue invalidation, late-result rejection | Fault-injection tests during each active command type |
| Previously accepted target continues | Validated pilot-mode transition or other safe action | SITL dynamics and bench command/mode evidence |
| Unintended spraying | Onboard expiring permission, hard shot timeout, hardware default-off | LED/dummy-load tests, reset/power sequencing, water tests |
| Stale or invalid height data | Sensor validity/age checks and spray inhibit | Sunlight, grass, minimum-range and dropout tests |
| Ground station or AI process fails | Independent RC recovery path and validated onboard failsafe | Separate fault tests with measured response times |
| Unsafe firmware/configuration assumption | Version inventory, parameter backup, pinned source and reviewed diffs | Evidence on exact installed hardware and firmware |
| Dependency or agent introduces unsafe change | Scoped changes, tests, review and protected private repository | Reviewed diffs and repeatable test records |

## Non-negotiable constraints
- Simulation and read-only operation first.
- Props off and ESC power disconnected for real-controller bench work unless an approved procedure explicitly requires otherwise.
- No real spray or flight actuation until the relevant acceptance gates are passed and reviewed.
- The precise GCS failsafe action and height-control settings remain unselected until verified against the installed firmware.
- A software-only spray lock is insufficient; verify the electrical driver defaults off during boot, reset and signal loss.
