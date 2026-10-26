# Spray Drone Master Stack

Private project repository for the local-AI spot-spraying drone software stack.

## Project status

**Planning and validation stage.** This repository does not yet contain an operational flight-control or spray-control implementation. Do not connect it to a live vehicle or enable actuation on the strength of these documents alone.

## Start here

1. Read [the master reference](docs/MASTER_PLAN.md).
2. Follow [agent instructions](AGENTS.md) before changing files.
3. Review [architecture](docs/ARCHITECTURE.md), [requirements](docs/REQUIREMENTS.md), [interfaces](docs/INTERFACES.md), and [safety case](docs/SAFETY_CASE.md).
4. Read the [MAVLink Router and MAVProxy setup guide](docs/MAVLINK_ROUTING.md) before adding telemetry endpoints.
5. Run the [software-only dry-run](simulation/README.md) before moving toward SITL.

The master reference preserves the project's existing decisions and explicitly identifies unresolved engineering questions. It is a planning document, not a certification or proof of safe operation.
