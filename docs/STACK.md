# ArduPilot Precision Drone Spraying — Software Stack Summary

Research notes compiled 2026-09-26. Links are to real upstream docs/repos only.  
This is not an official ArduPilot guide.

---

## 1. Flight controller firmware — ArduPilot / ArduCopter

### Core

| Item | Link |
|------|------|
| Main firmware repo | https://github.com/ArduPilot/ardupilot |
| Copter user docs | https://ardupilot.org/copter/ |
| Dev docs | https://ardupilot.org/dev/ |
| Crop Sprayer (official) | https://ardupilot.org/copter/docs/common-sprayer.html |
| Autopilot output functions | https://ardupilot.org/copter/docs/common-rcoutput-mapping.html |
| AC_Sprayer source | https://github.com/ArduPilot/ardupilot/blob/master/libraries/AC_Sprayer/AC_Sprayer.cpp |
| Firmware limitations (feature cuts) | https://ardupilot.org/copter/docs/common-limited-firmware.html |

### Built-in crop sprayer (`AC_Sprayer`)

Copter can drive a **PWM pump** and optional **PWM spinner** (rotary atomizer). Flow rate scales with **horizontal ground speed**.

**Enable & map outputs**

- `SPRAY_ENABLE = 1` (then refresh params — other `SPRAY_*` are hidden until enabled)
- Pump: `SERVOn_FUNCTION = 22` (Sprayer Pump)
- Spinner (optional): `SERVOn_FUNCTION = 23` (Sprayer Mixer / spinner)
- Pilot on/off: `RCx_OPTION = 15` (Sprayer)

**Key parameters** (from official Crop Sprayer docs)

| Param | Role | Default (docs) |
|-------|------|----------------|
| `SPRAY_PUMP_RATE` | Pump % when vehicle travels **1 m/s** | 10% → 100% at 10 m/s (linear) |
| `SPRAY_PUMP_MIN` | Minimum pump % while spraying | 0% (stops when vehicle stops) |
| `SPRAY_SPEED_MIN` | Min ground speed to spray (cm/s) | 100 (1 m/s) |
| `SPRAY_SPINNER` | PWM to spinner while pump is on | (see param docs / source defaults) |

PWM range for the pump/spinner channels is set via the corresponding `SERVOn_MIN` / `SERVOn_MAX`.

**Hardware note:** Docs cite vehicles such as the EnRoute AC 940-D with PWM pump (spinner optional). Higher spinner PWM → faster rotation → smaller droplets.

**Flash caveat:** Many boards with only **1 MB flash** do **not** include the sprayer feature. Check Firmware Limitations before buying hardware.

### Mission / MAVLink control of sprayer

- **`MAV_CMD_DO_SPRAYER` (216):** mission DO command; **param1 = 1** enable, **param1 = 0** disable (when sprayer support is compiled in). Discussed in ArduPilot / Mission Planner issues; verify your GCS exposes it.
- **`MAV_CMD_DO_AUX_FUNCTION`:** run aux function **15** (Sprayer) with switch position 0/1/2 (low/mid/high) from a mission item — documented for Copter mission commands:  
  https://ardupilot.org/copter/docs/common-mavlink-mission-command-messages-mav_cmd.html  
  Aux functions reference: https://ardupilot.org/copter/docs/common-auxiliary-functions.html
- Alternatives: `DO_SET_SERVO` / `DO_SET_RELAY` for simpler on/off pumps (not speed-proportional AC_Sprayer logic).

### Relay / non-PWM pumps

For on/off or relay-driven pumps (no proportional PWM), map a relay and use `DO_SET_RELAY` / Lua / companion GPIO. That bypasses speed-based `AC_Sprayer` rate control — pair carefully with mission speed and nozzle choice.

### Precision / agriculture-adjacent firmware features

| Feature | Docs |
|---------|------|
| RTK GPS correction | https://ardupilot.org/copter/docs/common-rtk-correction.html |
| Terrain following | Copter terrain docs under https://ardupilot.org/copter/ |
| GeoFence / inclusion polygons | Mission + fence docs |
| Lua scripting | https://ardupilot.org/copter/docs/common-lua-scripts.html |

ArduPilot does **not** ship a full “variable-rate prescription map” spray engine in `AC_Sprayer`. Variable logic is typically custom (Lua, companion, or GCS mission structure).

---

## 2. Spray-specific extensions, Lua, community projects

### Official extension path: Lua scripting

| Item | Link |
|------|------|
| Lua scripting docs | https://ardupilot.org/copter/docs/common-lua-scripts.html |
| Example scripts (stable) | Linked from that page → `libraries/AP_Scripting/examples` on GitHub |
| Applets (ready-to-use) | `libraries/AP_Scripting/applets` on latest branch |
| API bindings docs | Generated under `libraries/AP_Scripting/docs` in the ardupilot repo |

**Common spray customizations done in Lua (community / PR discussion):**

- Stop spraying on **RTL / Land / mission complete** (core PR discussion favored Lua over hardcoding into `AC_Sprayer`):  
  https://github.com/ArduPilot/ardupilot/pull/28684  
  Suggested pattern uses `rc:run_aux_function(15, …)` for start/stop.
- Turn pump off when **tank empty** (e.g. flow meter / pump current proxy): discourse threads such as  
  https://discuss.ardupilot.org/t/turn-off-sprayer-pump-when-tank-gets-empty-using-lua-scripting/119091  
  (Do **not** use blocking wait loops in scripts — use the update loop.)

Enable: `SCR_ENABLE = 1`, place `.lua` under `APM/scripts` on the SD card (2 MB+ flash boards; not on typical F4 scripting-disabled boards).

### Community / research projects (ArduPilot-adjacent)

| Project | Notes | Link |
|---------|--------|------|
| **ardu-field-planner** | Offline field coverage (boustrophedon) planner + MAVLink upload/telemetry aimed at ArduCopter ag spraying | https://github.com/tecelskyyivan-alt/ardu-field-planner |
| **autonomous-spraying-drone** | YOLOv8 plant detection + MAVSDK + RPi GPIO pump (Pixhawk; PX4 *or* ArduPilot class hardware) | https://github.com/ats-02/autonomous-spraying-drone |

Treat community repos as **examples**, not certified farm systems. Validate licensing, safety, and regulatory compliance yourself.

### Custom firmware builds

- Custom Build Server / waf options if you need features stripped on flash-limited boards or want DDS/ROS 2.  
- Sprayer may need a board/firmware build that includes `AC_Sprayer` (not cut for 1 MB flash).

---

## 3. Companion computer software

### Official overview

| Item | Link |
|------|------|
| Companion computers index | https://ardupilot.org/dev/docs/companion-computers.html |
| Raspberry Pi via MAVLink | https://ardupilot.org/dev/docs/raspberry-pi-via-mavlink.html |
| ROS (classic MAVROS) | https://ardupilot.org/dev/docs/ros-connecting.html |
| ROS 2 over Ethernet / DDS | https://ardupilot.org/dev/docs/ros2-over-ethernet.html |
| BlueOS install (ArduPilot wiki) | https://ardupilot.org/dev/docs/companion-computer-blueos.html |
| Rpanion install (ArduPilot wiki) | https://ardupilot.org/dev/docs/companion-computer-rpanion-install.html |
| APSync (deprecated) | https://ardupilot.org/dev/docs/apsync-intro.html |

### Typical hardware

- **Raspberry Pi** 4/5, CM4/CM5 carriers (Holybro Pixhawk RPi baseboards, Aerium Pascal, etc.)
- **NVIDIA Jetson** carriers (Holybro Pixhawk Jetson, Aerium Lumen, ARK, etc.)
- Listed on the companion computers page above.

### Typical FC ↔ companion link

From RPi MAVLink docs:

- `SERIALn_PROTOCOL = 2` (MAVLink 2)
- `SERIALn_BAUD = 921` (921600) commonly for RPi UART
- Device often `/dev/serial0` on Pi after UART enable via `raspi-config`

### Software layers (current vs legacy)

| Software | Role | Status / notes |
|----------|------|----------------|
| **BlueOS** | Modular companion OS (web UI, extensions) | Actively used; https://github.com/bluerobotics/blueos |
| **Rpanion-server** | Web config: telemetry routing, video, logs, NTRIP, networking | Maintained; https://github.com/stephendade/Rpanion-server · https://docs.rpanion.com/software/rpanion-server |
| **mavlink-router** / **mavp2p** | Lightweight MAVLink routing to GCS + apps | Preferred when Pi is heavily loaded vs full MAVProxy daemon |
| **MAVProxy** | CLI GCS / router | Still useful for debug |
| **pymavlink** | Python MAVLink | Scripts, tooling |
| **MAVSDK** | High-level mission/control APIs | Used by some vision-spray demos |
| **MAVROS** (ROS 1) | ROS bridge | Legacy stacks still use it |
| **ROS 2 + MicroXRCE DDS** | Native topics (ArduPilot 4.5+ ethernet path) | Needs custom build with DDS; `DDS_ENABLE`, etc. |
| **DroneKit** | Older Python API | Still listed; many new projects prefer MAVSDK/pymavlink |
| **APSync** | All-in-one companion image | **Deprecated** per companion computers index |

### Spray-relevant companion roles

- Computer vision (weed/crop detection) → trigger spray zone or hover-spray.
- Prescription / zone logic from GIS maps.
- Video streaming + log offload.
- NTRIP client on the companion (Rpanion documents NTRIP support) forwarding corrections.

---

## 4. Ground Control Stations

### Primary options

| GCS | Repo / docs | Spray / grid notes |
|-----|-------------|--------------------|
| **Mission Planner** | https://github.com/ArduPilot/MissionPlanner · https://ardupilot.org/planner/ | Strong ArduPilot support; Auto WP **Grid** over polygon for lawnmower surveys; mission DO commands; RTK survey-in UI | 
| **QGroundControl** | https://github.com/mavlink/qgroundcontrol · https://docs.qgroundcontrol.com/ | Cross-platform; **Survey** pattern (transects, angle, turnaround, terrain following); Plan View patterns |
| **APM Planner 2** | https://github.com/ArduPilot/apm_planner · planner2 docs on ardupilot.org | Cross-platform MAVLink GCS; less common than MP/QGC for day-to-day Copter spray work |

Mission planning docs:

- Mission Planner waypoints/events: https://ardupilot.org/planner/docs/common-planning-a-mission-with-waypoints-and-events.html  
- QGC Survey pattern: https://docs.qgroundcontrol.com/Stable_V4.3/en/qgc-user-guide/plan_view/pattern_survey.html  
- QGC Plan View: https://docs.qgroundcontrol.com/Stable_V5.1/en/qgc-user-guide/plan_view/plan_view.html  

### Spray mission patterns (practical)

1. Draw field polygon → generate **grid / lawnmower / boustrophedon** (MP Grid, QGC Survey, or ardu-field-planner).
2. Set altitude, line spacing (swath width), speed (`DO_CHANGE_SPEED`), turnaround distance.
3. Enable sprayer at start of spray legs (`DO_SPRAYER` / `DO_AUX_FUNCTION` 15 / RC switch) and disable on turns if needed to reduce overspray.
4. Use geofence / exclusion polygons for obstacles, waterways, no-spray zones.
5. Prefer **RTK Fixed** before precision edge work.

Neither MP nor QGC is a full farm ERP; they are mission + vehicle GCS tools. Dedicated ag planners (see §5) fill coverage/liquid-estimation gaps.

### Custom GCS

- Build on **MAVSDK**, **pymavlink**, or QGC/MP plugins.
- Community: **ardu-field-planner** (spray-oriented coverage + live MAVLink).

---

## 5. Base station & coordinating software

### RTK / GNSS base (precision)

Official: https://ardupilot.org/copter/docs/common-rtk-correction.html  

Three supported patterns:

1. **Local RTK base on GCS USB** → MP/QGC survey-in → RTCM via MAVLink to vehicle GPS.  
2. **NTRIP** caster over internet → GCS forwards RTCM to vehicle.  
3. **Direct wireless** base UART2 → vehicle GPS UART2 (transparent radio/Wi‑Fi), often with auto survey-in on base GPS.

Monitor vehicle status: **RTK Float** → **RTK Fixed** before relying on cm-level tracks.

### Farm / spray operations coordination

| Tool | Fit | Link |
|------|-----|------|
| Mission Planner / QGC | Single-vehicle ops, RTK forwarding, mission upload | See §4 |
| ardu-field-planner | Field coverage + spray-oriented stats + MAVLink | https://github.com/tecelskyyivan-alt/ardu-field-planner |
| Rpanion-server NTRIP | Companion as correction client | Rpanion docs |
| Multi-UAV fleet planners | Mostly research / PX4–ROS stacks; **not** a single official ArduPilot farm fleet product | Evaluate case-by-case |

**Gap:** There is no single official ArduPilot “spray operations center” for multi-drone fleets, chemical inventory, and regulatory spray logs. Operators typically combine GCS + farm GIS + custom logging.

---

## 6. Suggested reference architecture (single spray hex/octo)

1. **FC:** Pixhawk-class (2 MB+ flash) running current ArduCopter with sprayer included.  
2. **GNSS:** F9P-class RTK on vehicle + local base or NTRIP.  
3. **Spray:** PWM pump on `SERVOn_FUNCTION=22`; optional spinner `=23`; RC option 15.  
4. **Scripts:** Lua for RTL/tank-empty shutoff.  
5. **Companion (optional):** RPi 5 or Jetson with BlueOS or Rpanion; mavlink-router; optional ROS 2 / vision.  
6. **GCS:** Mission Planner (Windows) or QGC (cross-platform) + survey grid; RTK survey-in.  
7. **Ops:** Export/upload coverage missions; log spray enable, speed, GPS quality for QA.

---

## 7. Quick link index (official / primary)

- Firmware: https://github.com/ArduPilot/ardupilot  
- Crop Sprayer: https://ardupilot.org/copter/docs/common-sprayer.html  
- Lua: https://ardupilot.org/copter/docs/common-lua-scripts.html  
- Companion index: https://ardupilot.org/dev/docs/companion-computers.html  
- RPi MAVLink: https://ardupilot.org/dev/docs/raspberry-pi-via-mavlink.html  
- ROS 2 DDS: https://ardupilot.org/dev/docs/ros2-over-ethernet.html  
- RTK: https://ardupilot.org/copter/docs/common-rtk-correction.html  
- Mission Planner: https://github.com/ArduPilot/MissionPlanner · https://ardupilot.org/planner/  
- QGroundControl: https://github.com/mavlink/qgroundcontrol · https://docs.qgroundcontrol.com/  
- BlueOS: https://github.com/bluerobotics/blueos  
- Rpanion-server: https://github.com/stephendade/Rpanion-server  

