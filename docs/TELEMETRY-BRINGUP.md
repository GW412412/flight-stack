# Telemetry bring-up

What to install, in what order, and what must be true before telemetry flows. Router config: [`config/mavlink-router.conf`](../config/mavlink-router.conf). Design: [DECISIONS.md](DECISIONS.md), [ARCHITECTURE.md](ARCHITECTURE.md). Updated 23 Oct 2026.

## Required before any telemetry flows

1. The Pixhawk 1 is powered and connected by USB, and shows up as a serial device (`/dev/ttyACM0`, or a stable path under `/dev/serial/by-id/`).
2. Your user is in the `dialout` group, and ModemManager is masked so it doesn't grab the port: `sudo systemctl mask --now ModemManager.service`. Log out and in again after the group change.
3. ArduPilot is sending streams on the USB port. The heartbeat is always sent, but the rest only flows for streams enabled by the `SR0_*` parameters or requested by a connected client. Set the `SR0_*` rates so the watchers get data even with no ground station attached.
4. `/var/log/mavlink-router` exists and is writable by the user running the router.
5. The two placeholder addresses in `config/mavlink-router.conf` (VM, GPU computer) are replaced with real ones, and UDP is allowed between the computers: 14550 to the VM, 14570 to 14572 to the GPU computer.
6. Exactly one program has the serial port open. It must be mavlink-router, so close anything else that opened it.

## Install order

**CPU-only computer** (Ubuntu 24.04 or 26.04 LTS)

1. **Base:** `sudo apt install git python3-pip pipx` and the `dialout` and ModemManager steps above.
2. **mavlink-router.** Build from source:
   - `sudo apt install git meson ninja-build pkg-config gcc g++ systemd`
   - `git clone --recurse-submodules https://github.com/mavlink-router/mavlink-router`
   - `cd mavlink-router && meson setup build . --buildtype=release && ninja -C build && sudo ninja -C build install`
3. **QGroundControl.** The AppImage, plus `libfuse2 libxcb-xinerama0 libxkbcommon-x11-0 libxcb-cursor0`. QGC listens on UDP 14550 by default.
4. **mavproxy:** `pipx install MAVProxy`. Terminal-only mode needs nothing else. The `--console` and `--map` windows need wxPython.
5. **Mission Planner VM.** A hypervisor with a Windows guest, Mission Planner installed in it, and a network mode that lets the router reach the guest. Mission Planner listens on UDP 14550.

**GPU computer**

6. **NVIDIA driver** for the RTX 3060, then **Ollama**.
7. **Python with pymavlink:** `pip install pymavlink` in a virtual environment, for the watchers.
8. **Models:** `ollama pull d1-omni`, `ollama pull laya`, and the 2B tag of `d3` (check its tags on its Ollama page).

**Conversation-model chain** (after telemetry works)

9. **A tool-calling Ollama model** such as `qwen3:8b`.
10. **mavlink-mcp**, from the pinned commit in the stack report. Read-only by default. Leave actuation off until the actuation tests begin.
11. **ollmcp** v0.35.1, with human approval on for every tool call.

**Not needed for telemetry:** the video pipeline (OpenCV plus YOLO) and the SITL simulator.

## Bring-up check

1. Start the router: `mavlink-routerd -c config/mavlink-router.conf`.
2. Open QGC. It should show the vehicle.
3. Run `mavproxy.py --master=udpin:127.0.0.1:14552`. It should print heartbeats and accept `mode`.
4. Open Mission Planner in the VM and connect to UDP 14550.
5. On the GPU computer, check that a watcher feed arrives:
   `python3 -c "from pymavlink import mavutil as m; c=m.mavlink_connection('udpin:0.0.0.0:14570'); print(c.wait_heartbeat())"`
6. Check that `/var/log/mavlink-router` gains a `.tlog` file.

## Known gaps

- `config/mavlink-router.conf` has not been run against a real router or Pixhawk 1 yet.
- Mission Planner and mavproxy also send heartbeats that keep the GCS failsafe timer alive. Only QGC's is blocked in the config. Which client supplies the failsafe heartbeat is not decided.
- Blocking QGC's heartbeat at the router is untested.
