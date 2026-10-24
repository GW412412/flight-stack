# Index

Map of the project. [DECISIONS.md](DECISIONS.md) is the master document: where anything else disagrees with it, DECISIONS.md wins.

Everyone's ideas are welcome. We value your expert knowledge and your contributions, and your thought alone is the only contribution required.

## Changelog

- 2026-10-25: Added the "LLM behind the sticks" stack ([LLM-STACK.md](LLM-STACK.md)): MAVProxy hub, MAVLink MCP server with FlightGate, Ollama agent, Open WebUI via mcpo, data-flow monitor, SITL bring-up, systemd units and tests.
- 2026-10-23: Created the decisions log, architecture diagram, telemetry bring-up doc, mavlink-router config and this index; corrected the stack report; added the supersede rule; README ground-station section updated; Kimi captures tracked.

## Master and current

- [DECISIONS.md](DECISIONS.md): dated decisions and open questions, by area. Master document.
- [ARCHITECTURE.md](ARCHITECTURE.md): one diagram of the whole stack, plus the rules and mission shape.
- [STACK-REPORT-2026-10-18.md](STACK-REPORT-2026-10-18.md): software stack inventory, settings, known traps and test sequence.
- [TELEMETRY-BRINGUP.md](TELEMETRY-BRINGUP.md): what to install, in what order, and what must be true before telemetry flows.
- [../config/mavlink-router.conf](../config/mavlink-router.conf): router config that fans telemetry out to each endpoint with per-endpoint filters.
- [../README.md](../README.md): project overview and ground-station tools.
- [LLM-STACK.md](LLM-STACK.md): LLM behind the sticks - MAVProxy, MAVLink MCP server, Ollama, Open WebUI, monitor; diagram, ports, bring-up and safety notes.
- [REFERENCES.md](REFERENCES.md): checked links to ArduPilot, MAVLink, QGroundControl and router sources.

## Research notes (26 Sep 2026, earlier than the decisions log)

- [STACK.md](STACK.md): ArduPilot software stack summary.
- [SYSTEM-SPEC.md](SYSTEM-SPEC.md): camera and radio choices.
- [INTEGRATION.md](INTEGRATION.md): modules, buses, data flow and authority order.
- [AIRFRAME.md](AIRFRAME.md): F450 airframe with Pixhawk and Pi 3B+.
- [MICRO-SPRAY.md](MICRO-SPRAY.md): lighter S500 airframe variant.
- [LIVE-PIPELINE.md](LIVE-PIPELINE.md): camera to spray command, on the ground station.
- [WEED-ID.md](WEED-ID.md): finding rosette weeds in orthomosaics.
- [LABELING-WORKFLOW.md](LABELING-WORKFLOW.md): labeling tiles and training a first model.
- [WAYPOINT-OPT.md](WAYPOINT-OPT.md): ordering weed targets to cut flight time and spray.
- [OBSTACLE-AVOIDANCE.md](OBSTACLE-AVOIDANCE.md): collision avoidance sensors and limits.
- [OPENIPC.md](OPENIPC.md): OpenIPC as the camera and video link.
- [OPENIPC-CAMERAS.md](OPENIPC-CAMERAS.md): OpenIPC camera and air-unit hardware.
- [DIVERSITY-RX.md](DIVERSITY-RX.md): dual-antenna receive for video, RC and telemetry.
- [LINK-CONSOLIDATION.md](LINK-CONSOLIDATION.md): whether one radio system can carry RC, video and telemetry.
- [VOICE-CONTROL.md](VOICE-CONTROL.md): voice commands limited to a whitelist of flight modes.
- [SIMULATION.md](SIMULATION.md): SITL, HIL and spray testing without chemicals.
- [OPENIPC-YOLO-BRIDGE.md](OPENIPC-YOLO-BRIDGE.md): experimental bridge from OpenIPC video through YOLO to the visual centering loop on UDP 5602; timing model and review issues T1 to T14. Never commands flight.
- [SETUP-NOTES.md](SETUP-NOTES.md): starter checklist for a first spray airframe.
- [GAPS.md](GAPS.md): gaps in the end-to-end system.
- [PRESENTATION.md](PRESENTATION.md): overview for non-technical readers.

## Configs and code

- [../config/README.md](../config/README.md): notes on the example parameter files in `config/`.
- [../drone_stack/README.md](../drone_stack/README.md): SITL launchers, middleware, the MCP server and the Ollama agent.
- [VISUAL_CENTERING.md](VISUAL_CENTERING.md): design note for visual centering and precise flight commands (one nudge, settle, hold, fresh frames).
- [VISUAL-CENTERING-PROTOTYPE.md](VISUAL-CENTERING-PROTOTYPE.md): experimental prototype (code and tests) to centre a detected target under the camera with measured GUIDED nudges (fixed 0.5 m or chosen by the vision model).

## Kimi captures

- `kimi-share*` files in this folder: captures of the shared Kimi chat "Drone Project Check-In", kept for other models and contributors to reference.
