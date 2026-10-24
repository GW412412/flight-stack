# OpenIPC to YOLO bridge (experimental prototype)

This bridge reads the OpenIPC camera video, finds a target with YOLO (or a coloured card on the bench), estimates when each frame was taken, and sends one detection per frame to the visual centering loop on UDP port 5602. It never commands the aircraft. It has been tested only with drawn and replayed frames on a development machine: never with a real OpenIPC camera, a real YOLO model, the radio link or the drone. Please review it critically, especially the timing model and the timing issues T1 to T14 below.

- Status: draft, for peer review. Stacked on the visual centering prototype (pull request #1).
- Contributed by Maya (AI agent, Sesame), at the request of the project owner, G (GW412412), October 2026.
- Related: [VISUAL_CENTERING.md](VISUAL_CENTERING.md) (design note), [VISUAL-CENTERING-PROTOTYPE.md](VISUAL-CENTERING-PROTOTYPE.md) (the centering loop this feeds), [OPENIPC.md](OPENIPC.md) (camera and video link research).

## Scope

In scope: getting a timestamped detection of one chosen target from the OpenIPC video into the existing centering loop, with every timing assumption stated.

Out of scope, deliberately:

- **Flight commands.** The bridge sends no MAVLink. The centering loop, through `DroneTools` and the safety gate, stays the only thing that moves the drone.
- **Spraying.** There is no sprayer or pump code here.
- **Changes to existing files.** No existing module was changed. The only edit outside the new files is one line in [INDEX.md](INDEX.md).

## Architecture and data flow

```text
OpenIPC camera
   | RTSP pull (port 554), RTP over UDP push (port 5600), or a video file
   v
vision_ingest.py      reader thread: decode, stamp arrival time, count reconnects (epoch)
   |                  LatestFrameSlot: holds the newest frame only, older ones dropped
   v
vision_clock.py       estimate WHEN the frame was taken, as a window [lo, best, hi]
   v
vision_detect.py      detector (YOLO or colour card) -> tracker ids -> sticky target pick
   v
openipc_yolo_bridge.py  optional heading lookup from flight-controller telemetry,
   |                    final age checks, convert to ground-station wall time
   v
UDP JSON to 127.0.0.1:5602   (or an in-process LatestDetectionBox)
   v
visual_centering.py   existing centering loop (unchanged) -> DroneTools -> safety gate -> Pixhawk
```

### The modules

| File | What it does |
|---|---|
| `drone_stack/middleware/vision_ingest.py` | `Frame`, the newest-frame mailbox `LatestFrameSlot`, `OpenCVStreamSource` (RTSP, RTP through GStreamer, or a file, with reconnects and the camera password hidden in logs), `ReplayFrameSource` for tests, and `gst_rtp_pipeline()`. |
| `drone_stack/middleware/vision_clock.py` | The clock domains, `GsClock` (wall-clock step detection), `OneWayOffsetEstimator`, `CaptureTimeEstimator`, and `TelemetryAligner` / `MavNodeTelemetryFeed` for heading from the flight controller's own clock. |
| `drone_stack/middleware/vision_detect.py` | `UltralyticsYoloDetector` (refuses to download weights), `ColorBlobDetector` for bench cards, `CentroidTracker`, and `TargetSelector`, which sticks to one target. |
| `drone_stack/middleware/openipc_yolo_bridge.py` | `VisionBridge` (the pipeline), settings loader, timing budget report, UDP sink, optional slow "observer" model hook, benchmark and command line. |
| `config/vision_bridge.example.toml` | Example settings. Every timing number in it is a placeholder. |
| `drone_stack/middleware/tests/test_vision_bridge.py` | 36 tests (one needs a working OpenCV and is skipped otherwise). |
| `drone_stack/middleware/tests/conftest.py` | Shared fakes (`Clock`, `FakeTools`, `CAM`) and the `airborne` fixture, used by this test file and the centering tests. |
| `drone_stack/middleware/requirements-vision.txt` | The bridge's extra packages (numpy, OpenCV). Ultralytics is listed as a by-hand install because of its AGPL licence. |

### What each frame goes through

In `VisionBridge._process`, in order. Each drop is counted under the key shown, so `stats()` says exactly why frames were not published.

1. Drop if it sat in the mailbox longer than `max_frame_age_s` (`waited_too_long`) or comes from before the last reconnect (`old_epoch`).
2. Estimate the capture time. Drop if there is none (`no_capture_time`), if the window is wider than `max_capture_uncertainty_s` (`capture_too_uncertain`), or if it was captured no later than an earlier frame (`out_of_order`).
3. Run the detector, assign track ids, pick the target. Drop on a detector crash (`detector_error`) or no acceptable target (`no_target`).
4. If telemetry is on, look up the heading over the whole capture window. Drop if there is none (`no_heading`) or it changed more than `max_heading_spread_deg` (`heading_changing`).
5. Convert to wall time. Drop if the ground-station wall clock stepped while the frame was in the pipeline (`gs_clock_stepped`).
6. Final age check against `max_publish_age_s` (`too_old_to_publish`), then publish.

### What is published

The same `visual_centering.Detection` JSON the centering loop already reads, plus extra keys that its `from_dict` ignores:

- `t` is the **earliest** plausible capture time, in seconds on the ground station's `time.time()` clock (see T6).
- `t_best` and `t_latest` are the middle and latest plausible capture times.
- `t_method`, `t_assumed` and `t_uncertainty_s` say how the time was worked out, whether it rests on an unmeasured number, and how wide the window is.
- Also: `heading_spread_deg`, `class_name`, `track_frames`, `detector` and `producer`.

## Setup

Run everything from `drone_stack/middleware`, in the virtual environment from `requirements.txt`.

The bridge needs packages that `requirements.txt` does not list. They are in `drone_stack/middleware/requirements-vision.txt`:

```bash
pip install -r requirements.txt -r requirements-vision.txt
```

- `numpy` for the colour-card detector and the benchmark.
- OpenCV (`opencv-python`) to decode a camera stream or a file. The pip wheels have no GStreamer, so RTP over UDP needs an OpenCV built with GStreamer, or have the ground station restream it as RTSP.
- `ultralytics` only for the YOLO detector. It is left out of `requirements-vision.txt` on purpose: it is AGPL-3.0 and this repo is MIT, so the owner decides before it becomes the default detector. Install it by hand where you need it. The model file must already be on disk; the bridge will not download weights unless `allow_download = true`.

The camera password comes from the environment, never from the settings file:

```bash
export OPENIPC_PASSWORD=...
```

## How to run it

Check the timing budget before anything else. This prints the uncertainty window, the worst-case age the centering loop will see, and a warning for each timing issue that applies to your settings:

```bash
python openipc_yolo_bridge.py --config ../../config/vision_bridge.example.toml --budget
```

With the example settings it reports a 165 ms uncertainty window, a worst-case age of about 233 ms (200 ms assumed latency plus 33 ms assumed processing) against the loop's 500 ms limit, and that the first usable frame after each move arrives about 400 ms after the drone settles.

### Bench mode with a coloured card

No model and nothing sent to the loop. Point the camera at a red card and watch the detections print:

```bash
python openipc_yolo_bridge.py --config ../../config/vision_bridge.example.toml \
  --detector colour --no-udp --print
```

The card colour and tolerance are `colour_bgr` and `colour_tol` in `[detector]`. Use `--source recording.mp4` to replay a recorded flight instead of the live camera. The colour detector assumes one target in view.

To check the camera mapping end to end without anything being sent to the drone, leave out `--no-udp` and run the centering loop in plan-only mode beside it, as described in [VISUAL-CENTERING-PROTOTYPE.md](VISUAL-CENTERING-PROTOTYPE.md):

```bash
python visual_centering.py --config ../../config/llm_stack.pixhawk.toml \
  --camera-yaw-deg 0 --camera-calibrated --hfov-deg 80 --vfov-deg 64 --plan-only
```

Move the card around under the camera and check that the printed direction matches where the card is relative to the nose. The yaw and field-of-view numbers above are examples, not measurements.

### With YOLO

```bash
python openipc_yolo_bridge.py --config ../../config/vision_bridge.example.toml
```

Set `model_path`, `classes` and `class_names` for your weed model first. The bridge then sends to `127.0.0.1:5602`, where the centering loop listens by default.

### Other options

- `--source` overrides the video URL: an `rtsp://` URL, a file path, or `rtp` for the GStreamer RTP pipeline on `rtp_port`.
- `--replay-jsonl` replays a recorded timing script instead of a camera.
- `--benchmark N` times N drawn frames through the bridge and exits.
- Telemetry heading alignment and the slow observer model need wiring in code (an in-process `MavNode` and a `VisionObserver`). The command line switches both off and says so.

### Tests

```bash
python -m pytest -q tests
```

On this branch: 139 passed, none skipped, with OpenCV 5.0.0 installed. That is the 103 tests from the centering prototype plus the 36 new ones. Without an OpenCV that loads, the one test that decodes a real video file is skipped.

The new tests cover stale, out-of-order and pre-reconnect frames; clock offsets and jumps, a ground-station clock step and a flight-controller reboot; disconnects and the mailbox never holding more than it should; YOLO result parsing with a stand-in (no model needed); and one end-to-end run: drawn camera frames, bridge, port 5602, centering loop, the real flight tools and safety gate, then the pretend drone, with heading from flight-controller telemetry. That run centred the target within 35 cm with no mode changes and no arming.

## The timing model

The centering loop decides whether a detection is fresh enough by comparing `Detection.t` with `time.time()`. That only works if `t` really is when the picture was taken. The bridge cannot know that exactly, so it estimates a window and says how good the estimate is.

### What OpenIPC documents

Checked against the OpenIPC wiki on 25 October 2026, not tested on hardware:

- RTSP (port 554) and RTP over UDP carry RTP timestamps, whose starting value [RFC 3550 section 5.1](https://www.rfc-editor.org/rfc/rfc3550) says should be random. The [Majestic configuration](https://github.com/OpenIPC/wiki/blob/master/en/majestic-config.md) and [streamer](https://github.com/OpenIPC/wiki/blob/master/en/majestic-streamer.md) pages do not document RTCP sender reports or a per-frame wall-clock time on these outputs. So **the video gives no documented capture time.**
- Majestic's [analytics events](https://github.com/OpenIPC/wiki/blob/master/en/analytics-metadata.md) (motion and face detectors) do carry a capture time and a `pts`. But they are only sent for the camera's own detectors, nothing documented links that `pts` to the frames a ground-station decoder sees, and it is not stated whether the FPV build includes them. Listed as a future option, not used.
- An OpenIPC camera with no battery-backed clock has no trustworthy wall time until it hears a time server.

### The clocks

Every timestamp belongs to exactly one clock:

- **GS_MONO**: `time.monotonic()` on the ground station. Never steps. All the bridge's own bookkeeping uses it.
- **GS_WALL**: `time.time()` on the ground station. The centering loop's clock. It can step.
- **STREAM**: the decoder's presentation time. Arbitrary start, restarts on reconnect.
- **CAMERA_WALL**: the camera's own clock. Only meaningful if the camera is time-synced.
- **FC_BOOT**: flight controller `time_boot_ms`. Restarts when the flight controller reboots.

The bridge works in GS_MONO and converts to GS_WALL only at the moment it publishes.

### How capture time is estimated

Three methods, set by `[capture] method`:

- **`arrival` (default):** capture is arrival minus a latency between `min_latency_s` and `max_latency_s`, by default 35 to 200 ms. The 35 ms end comes from the 35 to 70 ms the OpenIPC APFPV guide quotes for its link; the 200 ms end leaves room for decode and buffering. **Both are assumptions until glass-to-glass latency is measured on this airframe.** Works with any transport.
- **`stream_pts`:** uses decoder timestamps to remove arrival jitter. A sliding minimum of (arrival minus pts) tracks the fastest delivery. The fastest delivery time itself cannot be seen from one-way data, so `min_latency_s` is still an assumption; only the jitter part is measured. Needs a decoder that reports real timestamps, which is unverified for OpenIPC.
- **`camera_wall`:** uses a camera clock capture time if a source provides one (only replay files today). Rejects a frame whose capture time is after its arrival.

The bridge publishes the **earliest** plausible time (`lo`) as `t`. The centering loop rejects frames that are too old or taken before the drone settled after its last move. Publishing the earliest time makes both checks lean towards throwing a frame away rather than acting on one that might be from before the move. The cost is that the uncertainty eats into the loop's age budget.

Every estimate carries an `assumed` flag. Treat any detection with `t_assumed: true` as resting on a placeholder until the latency is measured and `measured = true` is set.

## Timing issues for review (T1 to T14)

These are tagged in code comments where they apply. Each is a discussion point, not a solved problem.

1. **T1, latency is assumed.** The 35 to 200 ms range is a placeholder, not a measurement. Nothing about freshness is trustworthy until glass-to-glass latency is measured on the real link. The budget report warns while `measured = false`.
2. **T2, arrival is not capture.** The arrival stamp is taken after network transfer, jitter buffering and decode.
3. **T3, the wall clock can step.** `time.time()` can jump (NTP or chrony correction, a manual change). The loop measures age with it, so a jump makes every frame look stale or from the future. The bridge keeps its own times on the monotonic clock and drops any frame that was in the pipeline when a step was detected (threshold 50 ms).
4. **T4, the existing loop's heading is stamped at poll time.** `HeadingHistory` in `visual_centering.py` stamps each heading when it calls `get_status()`, not when the autopilot measured it. `MavNode` streams at 4 Hz by default, so a heading can be about 250 ms plus link delay older than its stamp. The bridge's optional `TelemetryAligner` uses the flight controller's own `time_boot_ms` instead, but needs an in-process `MavNode`.
5. **T5, slow model answers always lose.** The loop's UDP listener keeps only the newest detection by `t`. A slow vision model's answer describes an older frame than the fast detector's latest, so it would never win. With the observer on, the bridge publishes only the observer's frames (at most `max_rate_hz`, default 5 per second) and re-checks each one's age when the model finishes.
6. **T6, publishing the earliest time.** Safe for the age and settle checks, but the window eats into the 500 ms age limit. With defaults the worst case is about 233 ms. Reviewers: is earliest the right choice, or should the loop receive the whole window?
7. **T7, waiting after each move.** The loop wants a frame captured at least `settle_frame_delay_s` (200 ms) after the drone settles. With 200 ms worst-case latency, the first usable frame arrives about 400 ms after settling, which slows each step.
8. **T8, MavNode stamps telemetry with the wall clock.** It uses `time.time()` rounded to 1 ms. The feed converts with the offset at poll time and skips records received before a detected clock step.
9. **T9, unknown heading reads as 655.35 degrees.** `GLOBAL_POSITION_INT.hdg = 65535` means unknown; `MavNode` divides by 100 and reports 655.35. The bridge ignores it. The existing `HeadingHistory` does not check for it. This prototype does not change existing files, so this is flagged for a separate fix.
10. **T10, discontinuities.** A reconnect, a camera or flight-controller reboot, or a clock step changes the offsets abruptly. Each reconnect starts a new epoch; timestamps from different epochs are never compared, the estimators reset, and frames from an old epoch are dropped.
11. **T11, camera clock ahead of arrival.** In `camera_wall` mode, a capture time later than the frame's arrival means the clocks are not in sync. The frame is rejected.
12. **T12, shared clock between bridge and loop.** Both must read the same `time.time()`. On one machine over 127.0.0.1 they do. On two machines they must be synced well inside the age budget.
13. **T13, detector slower than the camera.** The mailbox keeps only the newest frame, so the published rate is the detector's rate, not the camera's. Overwritten frames are counted; a frame that still waited more than `max_frame_age_s` (150 ms) is dropped. Is 150 ms right once YOLO speed is known?
14. **T14, frame newer than the newest telemetry.** The bridge holds the last heading for up to `max_extrapolate_s` (150 ms), and only if the drone was yawing slower than `max_yaw_rate_dps` (10 degrees per second). Otherwise it drops the frame.

## Measured and unmeasured

Measured on the cloud development machine, with drawn 640 by 480 frames, the colour-card detector, and no camera, network or video decode:

- **Bridge cost:** about 8.5 to 8.7 ms per frame on average across reruns (under 9 ms at the 95th percentile), about 115 frames a second. Run it yourself with `--benchmark 300 --detector colour --no-udp`.
- **Frames published:** 298 of 300. The first two are held back because a target must be seen in 3 frames before it is offered (`min_track_frames`).

About the earlier figure of 153 of 300: the drawn target used to jump from the right edge back to the left every 90 frames. The tracker saw a new object each time, and the target picker correctly kept waiting for the vanished one for `lost_after_s` (1 second) before switching. So roughly half the frames were dropped as `no_target`. That measured the sticky-target rule, not throughput. The drawn target now moves back and forth smoothly. Whether 1 second is the right hold time in real flight is an open question.

Not measured:

- YOLO speed on any machine, and on the ground station in particular.
- Camera, link and decode latency (glass to glass). This matters most.
- Whether the decoder reports real frame timestamps for an OpenIPC stream.
- Anything on the ground station itself.

## Hardware and calibration still needed

Before this can feed a real flight:

1. Measure glass-to-glass video latency on the real link, set `min_latency_s` and `max_latency_s`, then `measured = true`.
2. Test the readers against the real camera over RTSP and over RTP through GStreamer.
3. Check whether the decoder gives real frame timestamps, which would allow the `stream_pts` method.
4. Run a real weed model and measure its speed on the ground station.
5. Measure the camera mount angle and the lens field of view.
6. Get a proper height reading over uneven ground; the centering loop uses height above take-off for now.
7. A simulator run, then a low test flight with a hand on the mode switch.

## Credits

Written by Maya (AI agent, Sesame) at the request of G (GW412412), who owns and directs the project. Reviews and corrections from people and other AI models are welcome. The repository's credits are proposed in pull request #2 (`CONTRIBUTORS.md`).
