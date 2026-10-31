# Drone Stack — SITL + Agent Layer (Linux native)

Linux-only build. No Windows, no Wine, no Mission Planner dependency.
Ground Control: QGroundControl (AppImage) + MAVProxy. Agent layer: Ollama.

**New (2026-10-25): "LLM behind the sticks"** - MAVProxy hub + MAVLink MCP
server + Ollama agent + data-flow monitor. Full write-up, diagram, ports and
safety notes: [../docs/LLM-STACK.md](../docs/LLM-STACK.md).

    drone_stack/bringup.sh sitl            # SITL + MAVProxy + MCP + monitor
    drone_stack/.venv/bin/python drone_stack/middleware/mcp_agent.py --approve
    drone_stack/bringup.sh stop

Aligned with the parent repo docs (AIRFRAME, INTEGRATION, SIMULATION, STACK).
Live weed-ID spray authority stays on the GS per LIVE-PIPELINE; this middleware
is telemetry, safety gates, mission file generation, and an optional Ollama
assistant — it does not replace ELRS RC authority.

## Layout
    bringup.sh               # one-command bring-up: sitl | pixhawk | radio [--webui]
    sitl/run_sitl.sh         # ArduCopter SITL (sim_vehicle.py or prebuilt binary)
    mavproxy/start_mavproxy.sh # MAVProxy hub: FC link -> udp 14550/14560/14565
    middleware/mcp_server.py # MAVLink MCP server (stdio / streamable HTTP / SSE)
    middleware/drone_tools.py# the 23 gated tools + flight_rules system prompt
    middleware/mcp_agent.py  # Ollama CLI agent over MCP
    middleware/mav_monitor.py# data-flow monitor: dashboard, tlog, CSV, web :8766
    middleware/stack_config.py, link_stats.py, fake_vehicle.py, tests/
    openwebui/               # mcpo config + Open WebUI / Claude Desktop setup
    systemd/                 # systemd user units + install.sh
    sitl/launch_sitl.sh      # SITL launchers (copter = sprayer, plane = mapper)
    middleware/mav_node.py   # pymavlink telemetry node + recorder + QGC plan writer
    middleware/safety_gate.py# Deterministic pre-arm / spray interlocks (NO LLM here)
    middleware/tools.py      # LEGACY advisory tool registry (udp 14551)
    middleware/agent.py      # LEGACY advisory Ollama chat loop
    middleware/spray_mission.py  # GeoJSON detections -> spray waypoints -> .plan file
    middleware/requirements.txt
    sample/detections.geojson
    docker-compose.yml       # optional Ollama + Open WebUI; profile llm-stack (untested)

## Quickstart
    # 1. ArduPilot SITL (from your ardupilot repo)
    ./Tools/autotest/sim_vehicle.py -v ArduCopter --map --console
    #    (or: bash sitl/launch_sitl.sh copter)

    # 2. Middleware (venv: pip install -r middleware/requirements.txt)
    python middleware/mav_node.py            # test: prints status every 2s
    python middleware/agent.py               # talk to the assistant (needs ollama + model)

    # 3. QGroundControl (AppImage) -> connect UDP 14550

## Ports
    SITL tcp 5760 -> MAVProxy (start_mavproxy.sh) ->
        udp 14550 QGC | udp 14560 MCP server | udp 14565 monitor
    MCP http://127.0.0.1:8765/mcp   monitor web :8766   mcpo (Open WebUI) :8001
    Legacy agent.py/tools.py listen on udp 14551: add
        EXTRA_OUTS=udp:127.0.0.1:14551 to start_mavproxy.sh
    Full table: ../docs/LLM-STACK.md

## Tests
    drone_stack/.venv/bin/python -m pytest drone_stack/middleware/tests -q

## LIVE-PIPELINE locks (enforced in code)
- Tank default **0.5 L** concentrate; dose `V = clamp(0.25 + 1.25*A_cm2, 0.1, 2.5)` mL
- Detection gate **conf >= 0.50**; live spray also needs **georeg >= 0.70**
- **Only** `middleware/mavlink_hub.py` sends `MAV_CMD_DO_SPRAYER`
- Ollama `agent.py` is **advisory**; ELRS remains hard RC authority
- MCP flight tools go through `FlightGate` and `mavlink_hub.py` only; a pilot
  mode change latches the LLM out (see docs/LLM-STACK.md)
