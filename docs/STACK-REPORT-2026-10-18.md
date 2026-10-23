# Spray Drone Software Stack — Consolidated Report

**Date:** 18 October 2026 (corrections added 23 October 2026)
**Status:** Planning document. Nothing in this report has been installed, run or flown. Bench and field tests are pending.
**Purpose:** One reference for the full software stack, the settings we intend to use, the known traps and their fixes, and the test sequence.

> **Corrections in this edition (23 Oct 2026):**
> 1. **`FS_GCS_ENABLE=2` is not a "hold" option.** ArduPilot's GCS failsafe docs say value 2 ("Continue with Mission in Auto") is no longer used. The current choices are 0 Disabled, 1 RTL, 3 SmartRTL or RTL, 4 SmartRTL or Land, 5 Land, 6 DO_LAND_START or RTL, and 7 Brake or Land. For a 50 cm spray height, **7 (Brake or Land)** or **5 (Land)** is the closest to a "stop where you are" action. Use `FS_OPTIONS` bits only if you want it to carry on with the mission.
> 2. **Relay parameter names.** Newer ArduPilot versions (4.5 and later) set relays up per relay, with `RELAY1_FUNCTION`, `RELAY1_PIN` and `RELAY1_DEFAULT`. The older single `RELAY_DEFAULT` doesn't exist there. Check the names against your firmware version.
> 3. **EKF rangefinder height.** `EK3_RNG_USE_HGT` is a percentage of the rangefinder's maximum range, below which the EKF switches to rangefinder height. `EK3_SRC1_POSZ = 2` makes the rangefinder the main height source all the time. Use the threshold approach for low passes, and confirm both against the current docs before you set them.
> 4. **Heartbeat filter (Trap 1 fix).** Blocking QGroundControl's outbound HEARTBEAT at MAVLink Router hasn't been tested. Confirm it on the bench (test 4, run with QGC both open and closed) before relying on it.

---

## 1. The short version

- Milestone 1 (the flight controller talking to a local AI) can be built from existing parts. The chain is: Pixhawk 1 on USB, then MAVLink Router, then QGroundControl plus mavlink-mcp, then ollmcp, then a local Ollama model.
- Nobody has published this exact chain working on real hardware. The first job is to prove it on the bench with props off.
- The language model only makes coarse decisions. Fine positioning runs in our own target servo code. Only a deterministic Lua spray gate on the Cube can switch the pump.
- There are no confirmed hard blockers. Two open questions could change the design: whether the drone can hold a steady 50 cm over real grass, and whether weed detection is reliable enough in real light.
- This report adds three settings to the original assessment: blocking QGC's heartbeat at the router, forcing the pump relay to default off, and using rangefinder height in the EKF for low hover.

## 2. Software stack inventory

### 2.1 Ground station

| Component | Role | Version / source | Notes |
|---|---|---|---|
| Ubuntu Linux | Host OS | 24.04 or 26.04 LTS preferred | 22.04 needs QGC v5.0.x or a manual build |
| QGroundControl | Human ground station for setup, parameters, map and calibration | v5.1.5 AppImage (1 Oct 2026) | Runs natively. Install `libfuse2 libxcb-xinerama0 libxkbcommon-x11-0 libxcb-cursor0`, add your user to `dialout`, and run `sudo systemctl mask --now ModemManager.service` |
| MAVLink Router | Shares the Pixhawk 1's single port between programs, with per-endpoint message filtering | `mavlink-routerd` | Preferred over MAVProxy. MAVProxy with two `--out udp:` endpoints is the alternative |
| MAVProxy | Optional router and command-line ground station | — | Its chat module relies on OpenAI's Assistants API, which has been shut down, so treat it as reference code only |

### 2.2 AI chain

| Component | Role | Version / source | Notes |
|---|---|---|---|
| mavlink-mcp | MCP server that turns MAVLink into tools: status, params, arm, takeoff, goto, move, orbit, land, RTL, camera | GitHub, pinned to the 8 Oct 2026 commit (PyPI latest is 0.1.5) | Read-only by default. Flying needs `--enable-actuation`, and real hardware also needs `--allow-real-vehicle`. Never flown on real hardware |
| ollmcp | MCP client for Ollama that runs mavlink-mcp over stdio | v0.35.1 (30 Sep 2026) | Keep human-in-the-loop approval on. Test the camera with `--camera file:<image>` |
| Ollama model | Local model with tool calling | `qwen3:8b` (5.2 GB), or `qwen3-vl:8b` (6.1 GB) / `gemma4:12b` (7.7–8.0 GB) for vision | Sizes are downloads only. On 40 CPU threads it runs slowly until the RTX 3060 arrives, and the speed hasn't been measured |

### 2.3 Flight controller (Pixhawk 1)

| Component | Role | Notes |
|---|---|---|
| ArduPilot Copter | Stabilisation, height hold, geofence, failsafes and RC override | Lua needs at least 2 MB of flash. Ready-built Simulation on Hardware firmware exists only for the Cube Orange |
| Lua spray gate | The only code allowed to switch the pump | Runs on the Cube, so it keeps working if the ground station crashes |
| Relay output | Pump driver signal | The relay pin drives a MOSFET or relay board, never the 12 V pump directly. Fit a flyback diode. Set the relay's default to off |
| Rangefinder | Downward height at spray altitude | Not chosen yet. Check `RNGFND1_MIN` and `RNGFND1_MAX` against the sensor's real range |
| Simulation on Hardware | Bench testing on the real board | `SIM_OH_RELAY_MSK` enables relays. Back up parameters before switching firmware |

### 2.4 Code we have to write

| Program | Where | Rate | Job |
|---|---|---|---|
| Target servo | Ground station | 5–10 Hz | Tracks the chosen weed and sends bounded `MAV_FRAME_BODY_OFFSET_NED` corrections. Caps each step and the total distance, works within a time budget, gives up if the target is lost, and runs only in Guided |
| Camera and nozzle geometry | Part of the target servo | Every frame | Uses the camera-to-nozzle offset, the tilt or gimbal angle and the attitude so the nozzle point, not the image centre, lands on the weed |
| Frame timestamps | Ground station | Every frame | Pairs each frame with attitude, height and gimbal angle at the moment of capture. Measure the video delay with an LED flashed by a relay |
| Spray gate (Lua) | Cube | Deterministic | Fires one timed shot only when all of these hold: armed, in Guided, rangefinder healthy, height within the band around 50 cm, a fresh fire request and a recent ground station link. Cuts off on a mode change, failsafe, stale link, target loss or when the timer ends. Logs every shot |
| Model tools | Ground station | On demand | `propose_target`, `confirm_or_skip` and a status readout. There is no "pump on" tool |
| AI watchdog | Ground station | 1 Hz | Puts the drone into a safe mode if the model or the target servo stops answering |

### 2.5 Detector

| Stage | Tool | When |
|---|---|---|
| Baseline | OpenCV colour mask | First. Count misses and false hits on hundreds of your own photos taken in varied light |
| Escalation | Small YOLO model | Only if colour alone isn't good enough |

Whether mavlink-mcp can read OpenIPC's H.265 stream (over `rtsp://` or `udp://` through OpenCV and FFmpeg) hasn't been tested.

## 3. Milestone 1 chain (props off, ESCs unpowered)

```
Pixhawk 1 (USB /dev/ttyACM0, 57600)
  │  mavlink-routerd -e 127.0.0.1:14550 -e 127.0.0.1:14560 /dev/ttyACM0:57600
  ├─→ UDP 14550 → QGroundControl
  └─→ UDP 14560 → mavlink-mcp → (stdio) → ollmcp → Ollama model
```

1. Start the router.
2. Start QGC on 14550.
3. Start mavlink-mcp on UDP 14560. Add the actuation flags only when the actuation tests begin.
4. Start ollmcp with approval for every tool call.
5. Tighten mavlink-mcp's limits. The defaults are 120 m height, 500 m per move, 2 km per goto and 20 m/s.

## 4. Traps and fixes

**Trap 1: QGC can defeat the link-loss failsafe.** mavlink-mcp and QGC both send heartbeats as system ID 255. ArduPilot resets the GCS failsafe timer on a heartbeat from any ground station, so if the AI dies while QGC is open, nothing trips. The heartbeat also comes from the server, not from the model, so a hung model trips nothing either.
- Primary fix (untested): block outbound HEARTBEAT on the QGC endpoint in MAVLink Router.
- Backup fix: the AI watchdog.
- Failsafe action: decide it deliberately in QGC. At a 50 cm spray height, consider `FS_GCS_ENABLE = 7` (Brake or Land) or `5` (Land) instead of RTL. Value 2 is no longer used.

**Trap 2: failsafe writes persist.** mavlink-mcp writes `FS_GCS_*` to the Pixhawk 1 on connect, and the values stay after the session. Policy: a person sets the failsafe parameters in QGC from the project parameter file, and after every session you compare the Pixhawk 1's parameters against that file.

**Trap 3: Simulation on Hardware fools the simulator check.** Any build with SIM_ENABLED sends SIMSTATE, so mavlink-mcp treats it as SITL and allows actuation. Motor outputs are blocked by default (`SIM_OH_MASK`), but keep the props off anyway.

**Trap 4: some commands take control back.** `arm`, `takeoff` and `orbit` switch the drone back to Guided. To take over again, flip the RC mode switch away and back. Approving each tool call is the guard against this.

**Smaller points.** Install mavlink-mcp from the pinned commit. An "arrived" result means within 2.5 m, which is not precision. `goto` keeps waiting until its timeout after you take over.

## 5. Configuration settings

### 5.1 MAVLink Router
- Endpoints: `/dev/ttyACM0:57600` to `127.0.0.1:14550` (QGC) and `127.0.0.1:14560` (mavlink-mcp).
- QGC endpoint: block outbound HEARTBEAT, then verify it in test 4.

### 5.2 Pixhawk 1 parameters (set by a person in QGC)

| Parameter | Intended value | Why |
|---|---|---|
| `FS_GCS_ENABLE` | 7 (Brake or Land) or 5 (Land). Project decision | Link-loss action at spray height |
| `FS_GCS_TIMEOUT` | 5 s | Confirm in test 4 |
| `FS_OPTIONS` | Bit 1 only if you want Auto missions to carry on through telemetry loss | Leave it off for Guided spraying |
| `FS_THR_ENABLE` | Choose deliberately (for example 5, SmartRTL or Land, or 7, Brake or Land) | RC link loss |
| `RELAY1_FUNCTION` / `RELAY1_PIN` / `RELAY1_DEFAULT` | Relay function, pump pin, default 0 (off) | Pump stays off at boot. Older firmware uses `RELAY_DEFAULT` |
| `EK3_RNG_USE_HGT` | Percentage threshold (or `EK3_SRC1_POSZ = 2`) | Rangefinder height for the 50 cm hold. Try this first in field test 4 |
| `RNGFND1_MIN` / `RNGFND1_MAX` | Set for the chosen sensor | Real minimum range |
| `FENCE_*` | Paddock fence on | Keeps the drone contained whatever the AI does |
| `SIM_OH_RELAY_MSK` / `SIM_OH_MASK` | Bench tests only | Relays on, motors blocked |

### 5.3 mavlink-mcp
- Pinned commit, read-only by default, and tight limits in the config file.
- Check what it writes on connect (listed in `describe_vehicle`).

### 5.4 ollmcp and Ollama
- Approval always on, and only one model loaded at a time.
- Log GPU memory and the time per decision from the first run.

### 5.5 Guided mode rules
- Prefer position targets, because they persist. Velocity targets must be re-sent at least once a second, and the drone stops about 3 s after the last one.
- Check that the drone is still in Guided before every send, and enforce the per-step and total-distance caps.
- Use terrain-frame `SET_POSITION_TARGET_GLOBAL_INT` for later terrain-following work.

## 6. Milestone 1 bench tests

1. **SITL only.** Ask the model to take off to 5 m, fly 10 m north and land. Pass when the QGC map matches the model's report.
2. **Real Pixhawk 1, read-only.** Ask for battery, GPS fix, mode, attitude and `RNGFND1_TYPE`. Pass when the answers match QGC and the attitude follows a hand tilt.
3. **Mode changes, props off.** Add the actuation flags and record the parameters written on connect. Flipping the RC switch must stop the AI's commands.
4. **Link loss.** Kill mavlink-mcp while in Guided, once with QGC closed and once with it open. Both runs must trigger the failsafe with the chosen action.
5. **Optional: Simulation on Hardware** (Cube Orange only). Drive an LED or water bottle from the relay. Back up parameters first.

## 7. Test sequence after milestone 1

1. Run the target servo and spray gate against SITL with a fake detector. Prove the cut-off on mode change, link loss and target loss.
2. Bench test the real Pixhawk 1 with the relay driving an LED or water bottle. Check the logs and the cut-off.
3. **Water on the bench:** measure shot volume, dribble and spray pattern at 50 cm. If it dribbles, fit a check valve or solenoid before any flying.
4. **Hover at 50 cm with water and no AI**, using rangefinder height in the EKF. Log the rangefinder against the barometer. This is the 50 cm gate.
5. Run the target servo over a coloured card with the model off. Measure the nozzle error.
6. Let the model choose targets with your approval for each one, over markers and then real weeds, still using water.
7. Use herbicide only after steps 1–6 pass repeatedly, and within local chemical and drone rules.

**Free gate to run in parallel:** detector validation on your own photos.

## 8. Hardware notes (engineering judgement)

- The pump has a pressure switch and no solenoid, so a Pixhawk 1 relay switches its power through a MOSFET. Fit a flyback diode. Line pressure can cause dribble after the pump stops (see test 3).
- Choose the rangefinder for how it behaves over grass and in sunlight at 50 cm.
- The Cube needs at least 2 MB of flash, and the Cube Orange is the only board with ready-built Simulation on Hardware firmware.

## 9. Provenance and open items

**Read directly:** mavlink-mcp source (8 Oct 2026 commit), ollmcp, the MAVProxy chat module, ArduPilot's GCS heartbeat code, build scripts, Lua functions and Simulation on Hardware board files, QGC install docs and releases, ArduPilot docs (Guided, terrain following, surface tracking, relays, Lua, GCS failsafe, Simulation on Hardware), the MAVLink Router sample config, and Ollama's model pages.

**Confirmed 23 Oct 2026 against the ArduPilot Copter docs:** the `FS_GCS_ENABLE` and `FS_THR_ENABLE` values and the `FS_OPTIONS` bits.

**Not verified:** OpenIPC video delay and MAVLink tunnelling, the exact Pixhawk 1 and Ubuntu versions you have, real model speed, the heartbeat filter fix, and anything on real hardware.

**Possible blockers:** the 50 cm hold (fallbacks are EKF rangefinder height, hovering higher with a longer jet, or a drop tube) and detection reliability (fallbacks are a measured colour baseline, then YOLO). Neither has to end the project.
