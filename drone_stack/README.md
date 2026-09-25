# Drone Stack — SITL + Agent Layer (Linux native)

Linux-only build. No Windows, no Wine, no Mission Planner dependency.
Ground Control: QGroundControl (AppImage) + MAVProxy. Agent layer: Ollama.

Aligned with the parent repo docs (AIRFRAME, INTEGRATION, SIMULATION, STACK).
Live weed-ID spray authority stays on the GS per LIVE-PIPELINE; this middleware
is telemetry, safety gates, mission file generation, and an optional Ollama
assistant — it does not replace ELRS RC authority.

## Layout
    sitl/launch_sitl.sh      # SITL launchers (copter = sprayer, plane = mapper)
    middleware/mav_node.py   # pymavlink telemetry node + recorder + QGC plan writer
    middleware/safety_gate.py# Deterministic pre-arm / spray interlocks (NO LLM here)
    middleware/tools.py      # Agent tool registry (schemas + dispatch + validation)
    middleware/agent.py      # Ollama chat loop calling tools
    middleware/spray_mission.py  # GeoJSON detections -> spray waypoints -> .plan file
    middleware/requirements.txt
    sample/detections.geojson
    docker-compose.yml       # optional Ollama + Open WebUI

## Quickstart
    # 1. ArduPilot SITL (from your ardupilot repo)
    ./Tools/autotest/sim_vehicle.py -v ArduCopter --map --console
    #    (or: bash sitl/launch_sitl.sh copter)

    # 2. Middleware (venv: pip install -r middleware/requirements.txt)
    python middleware/mav_node.py            # test: prints status every 2s
    python middleware/agent.py               # talk to the assistant (needs ollama + model)

    # 3. QGroundControl (AppImage) -> connect UDP 14550

## Ports
    SITL -> MAVProxy out :14550  (QGC / anything)
    MAVProxy -> middleware :14551  (udp:127.0.0.1:14551)
