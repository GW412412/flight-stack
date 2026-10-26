# Architecture

**Status:** Proposed architecture; implementation and end-to-end behavior are not yet demonstrated.

## Intended chain

Cube flight controller USB → one MAVLink transport/router owner → QGroundControl and a command gateway → mavlink-mcp → ollmcp → local Ollama model.

MAVLink Router is the planned distribution service where required. MAVProxy is an optional SITL diagnostic/CLI alternative, not another unrestricted AI command route. Do not have both processes open the same serial device. See [MAVLink routing guide](MAVLINK_ROUTING.md) and the loopback-only [router example](../config/mavlink-router/sitl-local.conf.example).

All AI-generated vehicle commands must pass through one command gateway, regardless of process topology. The model proposes coarse actions; it does not close the fine-positioning loop and must not have a direct pump-on tool.

## Authority boundaries

- **Flight controller:** stabilization, onboard navigation and configured failsafes, geofence, and RC mode selection.
- **Pilot:** final authority. Explicit takeover withdraws AI authority and selects a separately validated pilot-control mode.
- **Command gateway:** enforces a latched AI-authority state, command freshness, allowlists, motion limits, cancellation and audit logging.
- **MAVLink Router / MAVProxy:** transport and diagnostics only; neither substitutes for command authorization.
- **Target servo:** bounded fine corrections only while authority, tracking, telemetry and flight-state conditions remain valid.
- **Onboard spray gate:** sole normal software owner of pump permission; must default off and fail off.

## Required separation

Intent parsing, mission management, telemetry, command authorization, MAVLink transport, target correction and spray control are separate responsibilities. Components communicate through documented, validated interfaces. No component may bypass the authority gateway.

## Unresolved feasibility gates

- Reliable height control around 50 cm over grass.
- Reliable weed detection under real lighting.
- Exact firmware-specific failsafe behavior and rangefinder configuration.
- Verified joystick takeover path, cancellation of accepted targets, and independent onboard spray inhibit.
- Pinned and audited versions/configurations of all external tools.
- Verified endpoint directionality and proof that no alternate MAVLink route can bypass the gateway.

Do not treat this architecture as proof that these gates have passed.
