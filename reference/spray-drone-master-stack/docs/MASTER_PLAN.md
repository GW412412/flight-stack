# Spray drone software stack: master reference v2

This is a planning reference for a local-AI spot-spraying drone, with a latched pilot-takeover requirement and an unresolved, firmware-specific failsafe action.

**Revision date:** October 18, 2026  
**Status:** Proposed architecture only. Nothing was installed, configured, run, or flown in preparing this revision. Software, parameters, and safety behavior require validation before use.  
**Basis:** The consolidated report supplied by G, the earlier stack assessment, and the corrections discussed in the call. This revision is not a fresh source-code audit or a certification of the supplied report's claims.

## 1. Revision decisions

1. Remove `FS_GCS_ENABLE=2` as a hold recommendation. It is not a Guided-to-Loiter setting. Leave the actual failsafe action pending the installed Copter version and bench evidence.
2. Treat `FS_GCS_ENABLE=7`, where supported, as the documented **Brake or Land** candidate, not a guaranteed hold or a direct Loiter command. Confirm its exact behavior on the installed firmware before choosing it.
3. `FS_OPTIONS` bit 4 (decimal contribution 16) permits continuation in an existing pilot-controlled mode on GCS loss. It does not turn Guided into Loiter. Preserve other intended bits; do not overwrite the whole mask with 16.
4. Add a **latched manual-takeover interlock**. Once the pilot withdraws AI authority, neither the model nor any queued or delayed command may restore it. Explicit pilot re-enablement is required.
5. Mark the heartbeat-filter configuration, relay boot behavior, rangefinder-height configuration, and version-specific claims as pending validation rather than settled fixes.

The proposed software chain remains plausible, but the exact end-to-end chain has not been demonstrated here on real hardware. The two main feasibility gates remain reliable height control around 50 cm over grass and reliable weed detection in real light. Neither is established by this document.

## 2. Architecture and authority

Proposed chain: **Cube USB → MAVLink Router → QGroundControl and a command gateway → mavlink-mcp → ollmcp → local Ollama model.** The implementation must ensure every AI-generated vehicle command passes through the gateway, regardless of how processes are arranged.

- **Flight controller:** stabilization, configured navigation and failsafes, geofence, RC mode selection, and the onboard spray gate.
- **Pilot:** final authority; G intends to take over manually with a joystick and says joystick control is already enabled. This is a user-reported setup, not a connection or control-path test performed here. Keep the implementation simple: an explicit joystick takeover action withdraws AI authority and selects the validated pilot-control mode. Joystick movement alone is not assumed to override Guided.
- **Language model:** coarse proposals and mission decisions only. It does not close the fine-positioning loop and has no direct pump-on tool.
- **Target servo:** bounded fine corrections while authority, tracking, and flight-state conditions remain valid.
- **Command gateway:** one enforcement point for AI authority, command freshness, mode-change permissions, and motion limits. A prompt or per-command approval is not this interlock.
- **Lua spray gate:** the only normal software owner of the pump output, subject to validating that other command paths cannot bypass that ownership.

Human approval remains enabled, but is a second layer. It cannot substitute for a persistent pilot-authority latch.

## 3. Software inventory

### Ground station

| Component | Purpose | Validation status |
|---|---|---|
| Ubuntu Linux | Ground-station OS | Exact installed release pending. Supplied report proposes 24.04 LTS or 26.04; verify package and application support locally. |
| QGroundControl | Human setup, calibration, map, telemetry and parameters | Native Linux deployment intended. Supplied v5.1.5 and its release date are inherited, not rechecked for this revision. |
| MAVLink Router | Owns the USB connection and distributes telemetry | Endpoint directions and filtering behavior must be demonstrated. |
| MAVProxy | Optional alternative router and CLI | Not required in the selected topology. |

The supplied QGC setup calls for `libfuse2`, `libxcb-xinerama0`, `libxkbcommon-x11-0`, `libxcb-cursor0`, serial permissions through `dialout`, and preventing ModemManager from claiming the port. Confirm dependencies for the actual Ubuntu release first. Masking a host service affects more than this project; check whether it is needed elsewhere before changing it.

The supplied assessment says MAVProxy's chat module depends on the retired OpenAI Assistants API. Treat it as reference code, not the selected local-model integration; recheck current code before making a deployment decision.

### AI chain

| Component | Intended role | Pending checks |
|---|---|---|
| mavlink-mcp | Vehicle telemetry and high-level tools | Pin an exact commit SHA, inspect capabilities, limits, default read-only state, real-hardware checks, and parameter writes. |
| ollmcp | Local MCP client and model tool bridge | Confirm approval behavior, image handoff, cancellation and timeout behavior. |
| Ollama | Local inference | Confirm tool calling, selected model availability, memory and latency. |

The supplied report names ollmcp v0.35.1 and an October 8 mavlink-mcp source revision. A date is not an immutable pin: record the actual commit SHA and installed package versions. Its PyPI 0.1.5/0.1.6 statements conflict and must be resolved before installation.

Candidate models inherited from that report: `qwen3:8b` for telemetry/tool tests, and `qwen3-vl:8b` or `gemma4:12b` for later vision evaluation. Availability, capabilities and fit are pending local verification. Reported download sizes of about 5.2 GB, 6.1 GB and 7.7–8.0 GB are not runtime memory budgets. Context, images and cache add memory. Measure CPU performance on the proposed 40-thread host, then RTX 3060 performance if installed. Load one model at a time initially.

### Flight controller and sensing

- ArduPilot Copter on the actual Cube model, firmware version and build recorded before configuration. Verify Lua support and available resources; the supplied flash-size shorthand alone does not establish compatibility.
- Onboard Lua spray gate with bounded shot timing and fault shutdown.
- Downward rangefinder selected and tested for minimum range, sunlight, grass response, stale data and dropouts.
- Pump driven through an appropriately rated MOSFET/relay interface, never directly from a Cube signal pin. Confirm suitable suppression for the actual inductive load and driver.
- Simulation on Hardware is optional. The supplied report identifies ready-built Cube Orange support and `SIM_OH_RELAY_MSK`/`SIM_OH_MASK`; confirm availability and output semantics for the exact firmware before use. Back up parameters before changing firmware, and follow the applicable wipe/restore procedure.

## 4. Latched pilot-takeover interlock

### Required states

| State | AI permission |
|---|---|
| Locked at boot/restart | Telemetry only; no AI actuation or mode changes. Pump permission off. |
| Enabled by pilot | Bounded approved commands allowed only while all other checks pass. |
| Takeover/fault latched | AI actuation blocked, queued work invalidated, spray permission withdrawn. |

**Booting, reconnecting, returning to Guided, receiving another heartbeat, or resuming model output must never clear the latch.** The model cannot call an unlock function.

### Inputs that withdraw authority

The chosen joystick takeover action must latch authority off. Prefer a clearly assigned joystick button/action that both withdraws AI authority and requests the validated pilot-control mode. Do not assume moving a stick alone revokes Guided commands. An unexpected departure from the allowed flight mode must also withdraw authority. Link/watchdog faults, stale telemetry and process restarts default to locked. Exact joystick mapping, mode, freshness tests and fault thresholds remain pending implementation and bench validation.

A mode command alone is insufficient as the only authority signal: the earlier report identified tools that can command Guided again. The joystick takeover event must also reach the command gateway and latch it off. Verify the actual joystick-to-QGC-to-flight-controller path and whether pilot input still reaches the vehicle when AI processes fail. If joystick control shares the ground station/router/link, it is not an independent backup for failures of those components; retain an independently validated RC recovery path for flight tests.

### Enforcement requirements

1. Route **all** AI command paths through the authority check, including arm, takeoff, orbit, mode changes, mission writes, parameter writes, relay/servo commands, raw MAVLink and target-servo output. Disable unneeded tools and prevent alternate routes around the gate.
2. On takeover, atomically deny further AI commands, cancel ongoing actions where possible, flush queued requests, and invalidate their authority generation. A late tool completion cannot execute in a new session.
3. Recheck authority immediately before sending each vehicle command, not only when the tool starts. Repeated motion streams must stop when authority is revoked.
4. Withdraw pump permission onboard. A ground-station-only lock cannot guarantee pump shutdown when the ground station is unavailable.
5. Explicit re-enablement requires a deliberate pilot action and valid current telemetry, suitable mode, healthy control link and cleared faults. Clear old targets and pending fire requests; require a fresh target/approval sequence.
6. Log authority transitions, triggering inputs, rejected commands and any re-enablement. Reconcile logs with flight-controller mode and output logs.

**Important boundary:** blocking future messages does not cancel a position target the flight controller already accepted. The tested takeover procedure must also transfer the vehicle into the pilot's intended mode or another validated safe action. The exact action is not selected here.

The gateway, independent onboard permission input and cancellation handling are requirements to build, not existing demonstrated features of mavlink-mcp.

## 5. Code to build

| Module | Responsibility |
|---|---|
| Command gateway/interlock | Pilot-authority latch, tool allowlist, freshness checks, cancellation, command limits and audit trail. |
| Target servo | Proposed 5–10 Hz bounded corrections; abort on target loss, exhausted distance/time budget, invalid authority or wrong mode. Actual rate and gains pending latency tests. |
| Camera/nozzle geometry | Account for camera-to-nozzle offset, lens calibration, attitude and measured gimbal angle where fitted. Aim the nozzle, not just the image center. |
| Timestamping | Pair frames with capture-time attitude/height/gimbal data; measure video delay and jitter, initially using a visible flashing LED. |
| Lua spray gate | Onboard permission checks, timed shots, shutdown and shot logging. |
| Model tools | Propose target, confirm/skip and read status. No direct pump-on tool. |
| Watchdog | Detect unhealthy model/servo/control chain, revoke authority and request the separately validated safe action. A proposed 1 Hz monitor is not a proven response budget. |

Do not mix motion representations casually. Select and verify MAVLink coordinate frame, type mask, units and control semantics for every command. BODY_OFFSET_NED position and velocity interpretations require specific tests. Repeated relative-position requests must not accumulate into unintended travel.

The supplied report describes persistent position targets and velocity targets that stop after roughly three seconds without updates. Treat these as firmware/message-specific behavior to verify, not a universal substitute for a takeover mechanism.

## 6. Spray gate requirements

A shot requires: armed state, allowed mode, valid pilot AI authority, healthy and fresh range data, height inside a measured band around the proposed 50 cm, a fresh authorized target/fire request, and fresh control-chain health.

- Off at boot, on reboot, on takeover, on mode change, on fault, on stale permission, on target loss and at shot timeout.
- Hard maximum shot duration and minimum rearm interval; one request cannot retrigger continuously.
- Unique request identity/expiry so replayed or delayed requests cannot fire.
- No alternative AI relay/servo command may bypass the gate. Audit actual output mapping and tool access.
- A lost ground-station target must revoke eligibility through an expiring onboard permission, not depend solely on a one-time cancel message arriving.
- Log request identity, time, position, range/height, shot length, authority state and reason for cutoff.

All thresholds and the permission protocol are pending design and test. Select output default parameters only after checking the installed firmware. The inherited `RELAY_DEFAULT=0` proposal does **not** by itself guarantee electrical off during bootloader activity, reset, an unconfigured pin or parameter reset. Verify the actual parameter name and scope, and provide hardware biasing that keeps the driver off without a valid command. Test power sequencing and controller/driver disconnects.

## 7. Configuration register

| Item | Current decision |
|---|---|
| Cube identity, Copter version/build | Pending inventory. |
| USB path and serial settings | `/dev/ttyACM0:57600` is an example, not a discovered device. Prefer stable device identification once known. |
| QGC / AI UDP endpoints | Proposed localhost 14550 / 14560; verify bind/connect direction and isolation. |
| QGC heartbeat filtering | Proposed, not yet proven. Confirm which router direction blocks QGC-to-vehicle heartbeats while preserving vehicle-to-QGC telemetry. |
| GCS failsafe action | **Unselected.** Do not use value 2 as hold. Evaluate documented choices against installed firmware and tests. |
| GCS timeout | 5 s is an inherited candidate; measure response and decide whether it suits the operation. |
| FS_OPTIONS | Record existing mask, select each desired bit deliberately, preserve intended bits. Bit 4 is optional pilot-mode continuation, not Guided hold. |
| Failsafe parameter ownership | Human-reviewed project parameter file; prohibit or patch conflicting automatic writes before actuation. A post-session diff alone is insufficient. |
| Relay defaults and electrical off | Pending firmware/output mapping and boot/power tests. |
| Rangefinder min/max | Use actual firmware parameter names and units, set from measured sensor capability. |
| Height-control strategy | **Pending.** Do not prescribe `EK3_SRC1_POSZ=Rangefinder` or `EK3_RNG_USE_HGT` as a proven 50 cm solution. Verify supported settings and distinguish EKF height source from Copter surface tracking. |
| Geofence | Select boundaries/actions independently of the AI; validate before powered flight. |
| mavlink-mcp limits and flags | Read-only first; inspect actual config and `--enable-actuation`/`--allow-real-vehicle` handling before use. |
| Joystick takeover/latch | User-selected approach. Joystick control reported enabled; explicit takeover mapping, mode transition, gateway latch and onboard spray-inhibit handling remain unverified/not implemented here. |

### Heartbeat and watchdog distinction

Blocking QGC heartbeats may make GCS-loss detection depend on the intended AI endpoint, but it does not prove model health. A live MCP server can continue heartbeating while the model or target servo is hung. Test those faults separately. Verify which sender IDs the installed Copter actually monitors and ensure other endpoints cannot mask the test. The supplied claim about any GCS ID is not adopted as verified here.

Do not deploy the heartbeat change until its effect on human GCS operation and failsafe monitoring is understood. Keep the independent RC control path available.

## 8. Integration traps to retain

- Some tools may force Guided, including arm/takeoff/orbit in the supplied source assessment. The latched gateway must prevent this after takeover; approval alone is insufficient.
- A mode switch may only take effect when physically moved. Test the real switch behavior, including when a software command has changed the mode since the last movement.
- A tool can keep waiting after pilot takeover. Waiting is not authority; cancellation and late-completion rejection must be explicit.
- Reported mavlink-mcp arrival tolerance is 2.5 m. Confirm it in the pinned source; it is not nozzle aiming precision.
- Simulation-on-hardware may emit simulator indicators such as SIMSTATE. A message alone is not sufficient proof that outputs are safe or the board is not real hardware. Props remain off and ESC power disconnected on bench tests.
- Read-only behavior, automatic parameter writes and hardware opt-in checks must be inspected in the installed revision. Never rely on README descriptions alone.

## 9. Detector and camera gates

Start with a measured OpenCV color-mask baseline on hundreds of the user's own images across lighting and grass conditions. Record missed weeds and false targets, not just attractive demonstrations. Use a small YOLO detector only if the baseline is inadequate, then validate it on separate images and actual video.

OpenIPC video transport, H.265 decoding, UDP/RTSP compatibility and latency remain unverified. Confirm the exact stream and decoder before relying on the MCP camera path. A fixed camera is a simpler initial geometry; if gimballed, include measured angle and timing. Model vision must not become the flight stabilization loop.

## 10. Bench acceptance sequence

Props off and ESC power disconnected for real-controller bench work. Use an LED/dummy load before a pump, then water only.

1. **Inventory and backup:** record host OS, Cube hardware, firmware, parameter backup, RC map, exact software versions/commit SHAs and wiring. Resolve unsupported parameters before setting them.
2. **SITL basic chain:** request a 5 m takeoff, 10 m north movement and landing. Confirm model reports against actual telemetry, not prose alone.
3. **Real Cube read-only:** compare battery, GPS, mode, attitude and a known parameter against QGC; hand tilt must match telemetry. Confirm tools cannot actuate.
4. **Parameter-write audit:** identify every connection-time write. Remove or block ownership conflicts before proceeding, rather than restoring parameters afterward.
5. **Joystick authority latch tests:** start locked; enable only through deliberate pilot input. During pending arm, takeoff, orbit and movement requests, activate joystick takeover. Verify the selected pilot mode and actual joystick response, AI-command rejection and pump inhibition. Verify no delayed command, reconnect, model restart, new prompt, Guided entry or approval can unlock AI. Require deliberate pilot re-enablement and fresh requests. Disconnect the joystick and interrupt QGC/router/control transport separately to establish what fails and which independent recovery path remains.
6. **Mode takeover:** verify that accepted targets cease controlling the vehicle when the pilot takes over. Bench tests prove command/mode handling, not in-flight stopping dynamics; complete dynamics tests in SITL before flight.
7. **GCS-loss and health faults:** with the selected failsafe action explicitly recorded, test MCP exit with QGC closed and open. Separately hang the model, stop the servo, freeze telemetry, interrupt the gateway and reconnect processes. Record observed action and time for every case.
8. **Spray gate:** LED first. Prove timeout, stale permission, duplicate request rejection, target loss, mode change, authority revocation, range dropout and control-chain loss all prevent or terminate a shot. Test controller resets and independent driver power-up.
9. **Optional SoH:** only after exact board/firmware support is established. Verify motor-output isolation and relay behavior; back up and restore parameters using the applicable procedure.

Record firmware, settings, expected result, observed result, timing and logs for each test. Mark pass/fail explicitly. No flight gate is passed by an unobserved assumption.

## 11. Field progression

1. SITL target-servo and spray logic with fake detections; inject delay, stale messages and takeover during active actions.
2. Bench pump tests with water. Measure dose, dribble and spray pattern at the proposed 50 cm standoff. If residual pressure causes dribble, evaluate a suitable shutoff/anti-drip arrangement before flight; a generic check valve is not assumed to solve it.
3. Manual controlled hover without AI or spraying, with a safe test area and independent RC control. Assess rangefinder behavior, ground effect and height stability before committing to 50 cm over grass. Water payload may be carried only as appropriate for the test.
4. Target-servo trials over a marker, water only, after height and takeover gates pass. Measure nozzle-point error and stopping behavior.
5. Model-selected markers, then weeds, with approval per target and water only.
6. Herbicide only after repeated passes and applicable chemical-use and drone requirements are satisfied.

If 50 cm is not robust, reconsider height, nozzle, sensing or application geometry; a longer jet or drop tube is an alternative to evaluate, not a validated fix. If detection fails, improve the dataset and detector before adding autonomy.

## 12. Source register and remaining work

This revision preserves the supplied project's scope while separating proposals from established behavior. It does not independently reconfirm release dates, model specifications or all parameter claims. Use the installed firmware documentation and pinned source as the authority before implementation.

Reference entry points from the project material:

- [ArduPilot GCS failsafe documentation](https://ardupilot.org/copter/docs/gcs-failsafe.html)
- [ArduPilot Copter parameter reference](https://ardupilot.org/copter/docs/parameters.html)
- [ArduPilot Guided command documentation](https://ardupilot.org/dev/docs/copter-commands-in-guided-mode.html)
- [mavlink-mcp repository](https://github.com/deepakpopli/mavlink-mcp)
- [MAVLink Router repository and configuration](https://github.com/mavlink-router/mavlink-router)
- [QGroundControl Linux installation reference](https://docs.qgroundcontrol.com/Stable_V5.1/en/qgc-user-guide/getting_started/download_and_install.html)
- [MAVProxy chat module documentation](https://ardupilot.org/mavproxy/docs/modules/chat.html)

**Next concrete milestone:** complete inventory and read-only bench telemetry, then implement and fault-test the authority gateway before permitting AI actuation. The failsafe action, height-control settings, pump-off guarantees and exact version pins remain pending until evidence closes them.