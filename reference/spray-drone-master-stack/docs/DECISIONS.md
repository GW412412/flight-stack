# Decision register

This register records decisions and open items. Add dated entries when decisions change.

## D-001 — Preserve master reference v2
**Status:** Accepted as the current planning reference.

The existing master reference is retained at docs/MASTER_PLAN.md. It explicitly distinguishes proposals from verified behavior.

## D-002 — Simulation and read-only first
**Status:** Accepted.

No real actuation is enabled by the initial scaffold.

## D-003 — Pilot takeover must latch
**Status:** Required; implementation pending.

AI authority must remain withdrawn until an explicit pilot re-enable action. Reconnection or delayed work must not clear the latch.

## Open decisions
- Exact flight-controller hardware and firmware build.
- Firmware-specific GCS failsafe action and height-control configuration.
- Joystick takeover mapping and validated mode transition.
- Onboard spray permission protocol and electrical fail-off design.
- Exact pinned dependency versions and AI model selection.