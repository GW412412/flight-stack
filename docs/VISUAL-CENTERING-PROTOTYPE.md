# Visual centering: measured nudges in GUIDED

This is the flight-and-vision proof of concept that comes before any spraying. A detector reports where a target sits in the picture; the copter makes one measured move toward it, settles, holds, waits for a fresh picture, and repeats until the target is under the crosshair. No pump, no sprayer, no RTK and no weed geolocation are involved.

Code: `drone_stack/middleware/visual_centering.py`. Tests: `drone_stack/middleware/tests/test_visual_centering.py`.

## How one run works

1. You fly the copter yourself (or with the existing tools) into GUIDED, armed, hovering at least 2 m up. The controller never arms, takes off or changes mode.
2. It takes the newest detection. The frame must be fresh (default under 0.5 s old), confident enough (default 0.5), the locked target id, and inside the picture.
3. If the target is within the deadband (6 % of half the frame from the centre), it reports centred and stops.
4. Otherwise it picks a nudge in the camera frame (forward = top of the picture, right = right edge):
   - **Vision model's choice**: if the detection carries `nudge_forward_m` / `nudge_right_m`, that is used, clamped to `max_step_m` (1.0 m), ignored below `min_step_m` (5 cm), and refused if it points away from where the target is in the picture.
   - **Fixed step**: with no model nudge, 0.5 m toward the target ("forward point five"), or less if the target is closer than that.
5. The nudge is turned camera frame to body frame (camera mount) to north/east (heading at the time the frame was captured). `goto_relative` takes north/east, so this step matters.
6. The move goes through `DroneTools.goto_relative`, so the FlightGate still checks actuation, link, pilot override, RC inhibit switch, rate limit, geofence, altitude window and GUIDED.
7. It waits until the copter is within 0.25 m of the new spot and slower than 0.15 m/s, then calls `DroneTools.hold()` (which stays in GUIDED).
8. The next decision only uses a frame captured at least 0.2 s after settling. Perception can run at 30 fps; moves still happen one at a time.

## What stops a run, and what it does then

| Condition | Result |
|---|---|
| Mode no longer GUIDED (pilot switch, failsafe) | Stops. Sends no mode and no hold. GUIDED is never put back. |
| Pilot-override latch set | Stops, sends nothing. |
| Link stale, disarmed, below 2 m | Stops. |
| No usable frame or no plannable move for 3 s | Stops and holds. |
| Gate refuses a move (geofence, etc.) | Stops and holds. |
| 12 moves, or a move that would end over 5 m from the start | Stops and holds. |
| Rate limit | Waits, then decides again on a newer frame. |
| Stale / low confidence / other target / out of frame | That frame is skipped, no move. |
| Model nudge points away from the target | That frame is skipped, no move. |
| No heading sample within 0.3 s of the frame, or yawed over 5 deg since | That frame is skipped, no move. |

All limits live in `CenteringLimits`.

## Camera mounting (must be measured)

`CameraMapping` holds:

- `yaw_offset_deg`: angle from the nose to the top of the picture, clockwise seen from above. 0 means the top of the picture points at the nose.
- `mirrored`: the picture is flipped left to right.
- `hfov_deg`, `vfov_deg`: lens field of view. With height above ground these turn pixels into metres (pinhole model, camera straight down, flat ground). Without them the controller needs the model's nudge and cannot sanity-check it.

There is no safe default: a camera mounted 180 deg off will walk the copter away from the target (one of the tests shows the 5 m travel bound catching exactly that). The command-line runner refuses to start until `--camera-yaw-deg` and `--camera-calibrated` are given.

Height comes from `rel_alt` (height above home), not from a rangefinder. Over sloping ground that makes the pixel-to-metre scale wrong; ground following is a later step.

## Detector input

The detector process sends one JSON object per frame by UDP, default `127.0.0.1:5602`:

```json
{"t": 1792861380.571, "target_id": "weed-7", "confidence": 0.83,
 "cx": 402.5, "cy": 188.0, "image_w": 640, "image_h": 480,
 "nudge_forward_m": 0.35, "nudge_right_m": 0.20,
 "heading_deg": 91.5, "frame_id": "cam0-123"}
```

- `t` is the capture time in epoch seconds on the ground-station clock. Keep the camera and the ground station time-synced.
- `nudge_forward_m`, `nudge_right_m`, `heading_deg` and `frame_id` are optional.
- `detection_from_xyxy()` turns a YOLO-style box (x1, y1, x2, y2) into a detection without importing YOLO.

The live OpenIPC video to YOLO to UDP sender is not part of this change.

## Applying and running

From the repository root:

```bash
git am 0001-visual-centering.patch
cd drone_stack/middleware
. .venv/bin/activate            # the venv from requirements.txt
python -m pytest -q tests        # 84 tests: the 54 existing + 30 new
python visual_centering.py --demo                # model-chosen nudges, fake vehicle
python visual_centering.py --demo --fixed-step   # fixed 0.5 m steps
```

`--demo` starts the in-process fake vehicle on 127.0.0.1, arms it, takes it to 6 m and centres a target placed 1.4 m forward and 0.9 m left. It never touches real hardware.

### SITL with a pretend camera

The runner is its own MAVLink client, so give it its own MAVProxy output and do not run it next to the MCP server (that server's mode changes would trip this runner's pilot-override latch, and the other way round).

```bash
EXTRA_OUTS="udp:127.0.0.1:14570" drone_stack/mavproxy/start_mavproxy.sh   # plus SITL as usual
# In MAVProxy: mode GUIDED, arm throttle, takeoff 6
python visual_centering.py --config ../../config/llm_stack.sitl.toml \
  --mavlink-url udpin:127.0.0.1:14570 --camera-yaw-deg 0 --camera-calibrated \
  --hfov-deg 80 --vfov-deg 64 --sim-camera 1.5,-0.8 --model-gain 0.8 --live
```

`--sim-camera` is refused unless the config profile is `sitl`. Without `--live`, `dry_run` is forced on: the first move is planned and gated, nothing is transmitted, and the run ends.

### Bench check of the camera mapping (nothing is sent)

```bash
python visual_centering.py --config ../../config/llm_stack.pixhawk.toml \
  --camera-yaw-deg 0 --camera-calibrated --hfov-deg 80 --vfov-deg 64 --plan-only
```

With the detector running, move a target around under the camera and check that the printed `body` direction matches where the target is relative to the nose. `--plan-only` never calls an action tool.

## Implemented and still needed

Implemented and tested here:

- The controller, the camera to body to north/east conversion, the frame checks, the limits and stop rules above.
- The UDP JSON input and the YOLO box helper.
- A pretend downward camera, a demo, and tests against the fake vehicle through the real DroneTools, FlightGate, MavlinkHub and MAVLink path.

Still needed before a real flight:

- Measuring the camera mount (yaw, mirroring) and the lens field of view on the airframe.
- The detector process that reads the OpenIPC stream and sends the JSON above, and clock sync between camera and ground station.
- Video latency measured end to end; the 0.5 s freshness limit may need changing.
- A height source for the pixel scale over uneven ground (rangefinder or terrain), which ties into ground following.
- A SITL run, then a tethered or low-height test with your hand on the mode switch.
