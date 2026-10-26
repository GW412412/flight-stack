# Deployment plan

## Current status

Planning and scaffold stage. No deployment or flight test is claimed complete.

## Supported stages

1. **Developer workstation:** static checks and unit tests only.
2. **Simulation:** SITL with simulated vehicle and no real pump or actuator connected.
3. **Read-only bench:** telemetry reception and logging; no command transmission.
4. **Command-gateway bench:** props removed, ESCs disconnected, and commands directed only to mocks or a dummy load.
5. **Hardware-in-the-loop:** only after review of firmware versions, interfaces, and safety evidence.
6. **Controlled field evaluation:** requires a separate documented go/no-go review, applicable legal approvals, and verified emergency procedures.

## MAVLink transport

Use the [MAVLink routing guide](MAVLINK_ROUTING.md). Start with localhost-only SITL endpoints. MAVLink Router is the planned distribution service; MAVProxy is an optional diagnostic alternative. Only one process may own a given serial connection. Do not give an AI tool an unrestricted alternate MAVLink output, and do not treat router setup as proof that commands pass through the authority gate.

The example configuration under `config/mavlink-router/` is a template, not a tested deployment configuration. Verify syntax, endpoint direction and port ownership against the installed versions before launch. No serial device, remote host, or real vehicle is configured by default.

## Default configuration

- Environment: simulation.
- Actuation: disabled.
- Unknown settings: reject startup.
- Secrets: environment or local secret store only; never commit them.
- Logs and test results: local by default; scrub sensitive information before sharing.

## Release gates

Before advancing a stage, record the exact software versions and commit IDs, test commands, pass/fail results, outstanding hazards, and reviewer approval. A successful unit-test run alone does not establish vehicle safety.

## Flight readiness

This repository is not flight-ready. Firmware-specific failsafe behavior, geofence behavior, takeover persistence, target detection, altitude performance and the independent spray interlock remain to be verified. Do not connect chemicals or enable spray output during early validation.
