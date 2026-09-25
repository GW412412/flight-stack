# Setup notes — starter checklist

Practical ordering for a first ArduCopter spray airframe.  
**Examples only** — calibrate everything for your hardware. Not an official ArduPilot procedure.

Research date context: 2026-09-26.

---

## A. Before you buy / flash

- [ ] Confirm flight controller firmware build **includes sprayer** (many **1 MB flash** boards omit it). See Firmware Limitations: https://ardupilot.org/copter/docs/common-limited-firmware.html  
- [ ] Prefer **≥ 2 MB flash** if you also want **Lua scripting**.  
- [ ] Choose PWM-capable pump (or plan relay + accept no built-in speed-proportional flow).  
- [ ] Plan RTK if you need cm-level edge accuracy: https://ardupilot.org/copter/docs/common-rtk-correction.html  
- [ ] Decide companion need: vision / ROS / logging → RPi or Jetson; else FC + GCS may be enough.

## B. Bench / hangar — flight controller

1. Flash current stable **ArduCopter** from https://firmware.ardupilot.org or Mission Planner.  
2. Complete standard Copter setup (frame, ESCs, compass, accel, radio, failsafe).  
3. Enable sprayer:
   - `SPRAY_ENABLE = 1` → refresh parameters.  
   - Map pump output: `SERVOn_FUNCTION = 22`.  
   - Optional spinner: `SERVOn_FUNCTION = 23`.  
   - Pilot switch: `RCx_OPTION = 15`.  
4. Set `SERVOn_MIN` / `SERVOn_MAX` to the **actual** PWM range of your pump ESC/driver.  
5. Start with conservative `SPRAY_PUMP_RATE`, `SPRAY_PUMP_MIN`, `SPRAY_SPEED_MIN` — see official docs:  
   https://ardupilot.org/copter/docs/common-sprayer.html  
6. Use propellers-off ground tests: enable sprayer via RC option 15; verify pump PWM in GCS Servo output / logs while simulating motion (or use sprayer test APIs if available in your version).  
7. **Never** spray chemicals on people; use water for first flights.

## C. Rate calibration (critical)

`SPRAY_PUMP_RATE` is **% pump at 1 m/s**, linear with ground speed. Defaults are **not** your L/ha.

Suggested approach (adapt to local ag practice):

1. Measure tank volume and nozzle flow at known PWM / pressure on the ground.  
2. Fly a measured transect at constant speed with sprayer on; measure volume used and distance.  
3. Adjust `SPRAY_PUMP_RATE` (and possibly `SPRAY_PUMP_MIN` / `SPRAY_SPEED_MIN`) until applied volume matches target L/ha for your swath width.  
4. Re-check at a second speed.  
5. Document nozzle type, pressure, height AGL, and wind limits in your ops notes.

## D. Mission structure (starter)

Typical AUTO mission sketch:

1. `TAKEOFF` to spray altitude.  
2. Fly to field entry.  
3. `DO_CHANGE_SPEED` to spray ground speed.  
4. Enable sprayer: `DO_SPRAYER` (216) param1=1 **or** `DO_AUX_FUNCTION` aux=15 high **or** rely on RC switch / Lua.  
5. Grid waypoints (from MP Grid / QGC Survey / field planner).  
6. Disable sprayer on turnarounds if overspray is an issue.  
7. Disable sprayer before RTL/LAND.  
8. `RTL` or `LAND`.

Mission command reference:  
https://ardupilot.org/copter/docs/common-mavlink-mission-command-messages-mav_cmd.html  

Grid / survey:

- Mission Planner: https://ardupilot.org/planner/docs/common-planning-a-mission-with-waypoints-and-events.html  
- QGC Survey: https://docs.qgroundcontrol.com/Stable_V4.3/en/qgc-user-guide/plan_view/pattern_survey.html  

## E. Lua helpers (recommended)

Enable scripting: `SCR_ENABLE = 1`, scripts in `APM/scripts`.  
Docs: https://ardupilot.org/copter/docs/common-lua-scripts.html  

Prioritize:

- Force sprayer **off** in RTL / Land / Disarm.  
- Force **off** when tank empty (flow sensor, level switch, or pump current threshold — validate thoroughly).  
- Avoid blocking `while` wait loops (scripts will be killed).

See example sketch notes under `scripts/` in this repo.

## F. Companion computer (optional)

Follow: https://ardupilot.org/dev/docs/raspberry-pi-via-mavlink.html  

Minimum:

1. UART: FC TELEM ↔ Pi TX/RX/GND; power Pi from adequate 5 V supply (not usually FC TELEM 5 V).  
2. FC: `SERIALn_PROTOCOL = 2`, `SERIALn_BAUD = 921`.  
3. Pi: enable UART, use `/dev/serial0`.  
4. Install **Rpanion-server** or **BlueOS**, or run **mavlink-router** + your app.  
5. Do **not** start new projects on **APSync** (deprecated).

ROS 2 / DDS path (advanced): https://ardupilot.org/dev/docs/ros2-over-ethernet.html  

## G. RTK base station

1. Survey-in local base in Mission Planner or QGC, **or** use NTRIP credentials.  
2. Confirm vehicle HUD shows **RTK Fixed** before precision missions.  
3. Save surveyed base position for reuse if the tripod is relocated exactly.  

Docs: https://ardupilot.org/copter/docs/common-rtk-correction.html  

## H. First flight progression

1. No liquid — verify AUTO grid tracking only.  
2. Water — short strip, measure deposition pattern (cards / dye if appropriate).  
3. Water — full field at target speed/height.  
4. Chemical — only after SOP, PPE, regulatory, and weather checks.

## I. Safety / regulatory (non-exhaustive)

- Treat spray drone as both **aircraft** and **pesticide applicator** under local law.  
- Geofence no-spray zones; keep visual observers as required.  
- Failsafe: plan what happens to the pump on radio loss / RTL (Lua off is strongly recommended).  
- Log GPS quality, spray enable state, and speed for post-flight QA.

## J. Repo-local files to copy carefully

| File | Use |
|------|-----|
| `config/example-spray-params.param` | Starting `SPRAY_*` / SERVO function sketch — **edit channel numbers** |
| `config/example-companion-serial.param` | Example TELEM2 MAVLink settings |
| `scripts/example-sprayer-rtl-off.lua` | Sketch for RTL/Land shutoff — test in SITL first |

