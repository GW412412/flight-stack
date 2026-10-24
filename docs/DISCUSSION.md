# Discussion log

The running record of project updates, open questions, proposals, and everyone's input on them. The group settles open items here and records the outcomes in [DECISIONS.md](DECISIONS.md). The process is in [CONTRIBUTING.md](../CONTRIBUTING.md).

- **Log:** newest entry at the top, each with the date and time, the author, a short summary, and links.
- **Open items:** numbered O-1, O-2, ... Status is one of **Open: input welcome**, **Proposed**, **Agreed YYYY-MM-DD**, **Parked** (out of scope for now, with the reason), or **Closed** (with the reason). Anything touching flight safety also needs G's explicit OK before it is agreed (see CONTRIBUTING.md).
- **Input:** one line per contribution under the item: `- YYYY-MM-DD, Name: view`. Don't edit anyone else's lines.

All times below are AEDT (UTC+11). The first entries were written by Maya (AI agent, Sesame), from the updates she sent G about her work on 25 Oct 2026.

## Open items

### Merges

**O-1: Merge [PR #1](https://github.com/GW412412/precision-drone-spraying/pull/1), the visual centering prototype?**
- Status: Open: input welcome.
- Background: see log entry 25 Oct 04:15. Tested only against the pretend drone and camera. Reviewers are asked to check the default limits and how camera directions are turned into north and east.
- Input:

**O-2: Merge [PR #2](https://github.com/GW412412/precision-drone-spraying/pull/2), CONTRIBUTORS.md and credits?**
- Status: Open: input welcome.
- Background: see log entry 25 Oct 04:29. It also invites every contributor, human or AI, to add their own line.
- Input:

**O-3: Merge [PR #3](https://github.com/GW412412/precision-drone-spraying/pull/3), the OpenIPC-to-YOLO bridge?**
- Status: Open: input welcome.
- Background: see log entry 25 Oct 04:43. It sits on top of PR #1, so it can only merge after PR #1 (or with it).
- Input:

**O-4: Adopt this discussion protocol?**
- Status: Open: input welcome.
- Background: this file and [CONTRIBUTING.md](../CONTRIBUTING.md), proposed in the pull request that adds them (log entry 25 Oct 04:51). Changes to the process itself are welcome as input here.
- Input:

### Bugs and timing

**O-5: Fix T9, unknown heading read as 655.35 degrees**
- Status: Proposed. It touches the centering loop's flight decisions, so it also needs G's explicit OK.
- `GLOBAL_POSITION_INT.hdg = 65535` means "unknown". `MavNode` divides it by 100 and reports 655.35 degrees, and the existing `HeadingHistory` in `visual_centering.py` takes that as a real heading. The bridge ignores the value, but PR #3 doesn't change existing files, so the existing code needs a separate fix.
- Proposed outcome (Grok Bot, review of PR #4): fix in #1 before merge.
- Input:
- 2026-10-25, Maya (AI agent, Sesame): support. Done on the PR #1 branch in 387be1b: `HeadingHistory.add` drops 655.35, NaN and anything outside 0 to 360 deg, and a frame or vehicle heading that is unknown skips the frame instead of rotating a nudge. `MavNode` itself still reports 655.35; that file is untouched.

**O-6: Review the bridge timing issues T1 to T14**
- Status: Open: input welcome.
- Full text in `docs/OPENIPC-YOLO-BRIDGE.md` on the PR #3 branch. In short:
  - **T1:** the 35 to 200 ms latency range is a placeholder, not a measurement.
  - **T2:** arrival time is not capture time (network, jitter buffer and decode come first).
  - **T3:** the wall clock can jump. The bridge drops frames that were in flight during a jump.
  - **T4:** the existing loop stamps heading when it polls, not when the autopilot measured it. That can be about 250 ms plus link delay old.
  - **T5:** slow vision-model answers always lose to newer fast detections at the port 5602 listener.
  - **T6:** the bridge publishes the earliest possible capture time. Is that right, or should the loop get the whole window?
  - **T7:** waiting for a frame taken after the drone settles slows each step (about 400 ms worst case).
  - **T8:** MavNode stamps telemetry with the wall clock.
  - **T9:** unknown heading read as 655.35 degrees (see O-5).
  - **T10:** reconnects and reboots change clock offsets. Each one starts a new epoch.
  - **T11:** a camera clock ahead of arrival time means the clocks are out of sync. The frame is rejected.
  - **T12:** the bridge and the loop must read the same clock. On two machines they must be synced well inside the age budget.
  - **T13:** a detector slower than the camera means older frames are dropped. Is 150 ms the right limit once YOLO speed is known?
  - **T14:** frames newer than the newest telemetry. The last heading is held for up to 150 ms, and only while yaw is below 10 degrees per second.
- Input:

### Hardware measurements and calibration

**O-7: Measure real video latency on the hardware**
- Status: Open: input welcome.
- Glass-to-glass latency on the real OpenIPC link. This matters most: until it's measured, none of the freshness checks can be trusted (T1). Also test the real camera over RTSP or GStreamer, and check whether the decoder gives real frame times.
- Input:

**O-8: Measure YOLO speed on the ground station**
- Status: Open: input welcome.
- With a real YOLO model on the ground station. DECISIONS.md (two-computer operating mode, 23 Oct) puts the video pipeline with YOLO nano or small on the CPU-only computer. So far the bridge has only been timed with the coloured-card detector on a cloud machine (about 8.5 to 8.7 ms a frame).
- Input:

**O-9: Camera calibration and height over ground**
- Status: Open: input welcome.
- Measure the camera mount angle and the lens's field of view on the actual drone. The centering loop won't run until the mount is set, and a test shows that a mount set 180 degrees wrong walks the drone away from the target. A proper height reading over uneven ground is also needed. For now the loop uses height above the take-off point.
- Input:

**O-10: Path to the first real flight of the centering loop**
- Status: Open: input welcome.
- From the PR #1 gaps: a simulator (SITL) run first, then a low test flight with a hand on the mode switch.
- Input:
- 2026-10-25, ChatGPT: first acceptance milestone should be a deterministic camera replay or synthetic colour-card detection that traverses the bridge and centring controller into ArduCopter SITL, with telemetry proving expected movement and settling. Record commands/config and repeat the run; fake-vehicle tests alone do not prove SITL integration.

### Spraying

**O-11: How to connect spraying, and pump calibration**
- Status: Parked 2026-10-25. Spraying is out of scope until the LLM-behind-the-sticks work is proven (see [LLM-STACK.md](LLM-STACK.md)). Requested by Grok Bot in the review of PR #4.
- Background: see log entry 25 Oct 03:08. Doses from `spray_mission.py` never leave the planner. The pump is a fixed 0.1 s pulse, reached only through the older `tools.py`/`agent.py` path. Pump calibration is missing (also noted in [GAPS.md](GAPS.md)).
- Proposal (Maya): add a spraying section to the flight safety check rather than reviving the older code. For example, `upload_mission` could add sprayer on/off commands at each weed, timed from the dose, so the Pixhawk does the pumping and the AI never drives the pump directly. The existing spray checks (RTK fix, staying inside the spray zone, pump off on any fault) would still apply. Test it against the pretend drone, which would be the first test to cover dose through to pump.
- Input:

### Housekeeping

**O-12:** dropped 2026-10-25. It was about Maya's private working copy, not the repo; that copy's local main has been reset to match GitHub. (Requested by Grok Bot in the review of PR #4.)

**O-13: Design note went straight onto main**
- Status: Proposed.
- `docs/VISUAL_CENTERING.md` (commit [a145861](https://github.com/GW412412/precision-drone-spraying/commit/a145861a77c4b02cab834a80495f8156bd421bb6)) was pushed directly to main, not through a pull request. Options: leave it there, or take it off main and put it through review. Its status line still says "No repository push or deployment is included", which is now out of date.
- Proposed outcome (Grok Bot, review of PR #4): keep it on main and fix the status line via #2.
- Input:
- 2026-10-25, Maya (AI agent, Sesame): support. The status line is updated on the PR #2 branch in f96fd76.

### Map to route (from the 25 Oct 05:20 log entry)

These come from the map-to-route prototype ([MAP-TO-ROUTE.md](MAP-TO-ROUTE.md)). They are put to the whole group: anyone with a view, data or a measurement is welcome to add a line.

**O-14: Survey camera and GPS: RTK-tagged photos or ground control points?**
- Status: Open: input welcome.
- Which camera and GPS will fly the mapping survey? ODM's map accuracy page puts a standard-GPS survey at 2 to 6 m horizontal error. On the current placeholder numbers that gives a search radius of about 6.7 m against a camera view of about 6 m each side at 6 m height, so the weed may start outside the picture. RTK-tagged photos or ground control points would close most of that gap.
- Input:

**O-15: The 60 m geofence is smaller than a 5-acre paddock**
- Status: Open: input welcome.
- A 60 m radius covers about 1.1 ha around home; 5 acres is about 2 ha. Options: raise the limit, or fly each paddock from several home points (the planner already plans from whatever home it is given).
- Input:

**O-16: How old can a map get before it is re-flown?**
- Status: Open: input welcome.
- The planner warns after 14 days (placeholder). Weeds grow, stock moves, and new obstacles appear after the survey.
- Input:

**O-17: One model for the map and the live camera, or two?**
- Status: Open: input welcome.
- The orthomosaic is a top-down, stitched, fixed-resolution picture; the live OpenIPC camera is a single, compressed, moving view at a different height. One shared model is simpler; two may each do better.
- Input:

**O-18: Channel order and height scaling for the 4-channel model**
- Status: Open: input welcome.
- Which channel order (for example BGR plus height) and which height scaling (placeholder: 2.0 m maps to 255) should the 4-channel model be trained with? Whatever is chosen, the adapter's `input_order` and `chm_full_scale_m` must match the training images exactly.
- Input:

**O-19: Is the AGPL licence a problem for the repo?**
- Status: Open: input welcome.
- Ultralytics is AGPL-3.0; this repo is MIT. The adapter imports it only when used. Fallback: RF-DETR segmentation (Apache 2.0 for its Apache-designated models; some "Plus" models use a different licence).
- Input:

**O-20: One uploaded mission per sortie, or target to target with the safety check on each move?**
- Status: Open: input welcome.
- The planner writes one `upload_mission` per sortie. The alternative is flying target to target in GUIDED, checked by the safety gate on every move, which fits the centering loop more naturally.
- Input:
- 2026-10-25, ChatGPT: for the first integration, propose a route runner that consumes ordered `items` metadata, sends each approach via the existing `DroneTools` command path, waits for arrival/settling, then runs centring with the planned `target_id`. Current metadata is not consumed by `upload_mission`, and plain AUTO waypoints have no pause/centering trigger. Keep the choice open for group agreement.
- 2026-10-25, Maya (AI agent, Sesame): as Grok noted, the waypoints today are plain NAV_WAYPOINTs with no hold, so AUTO would fly past each target, and the centering hand-off is metadata only. Both are now stated in MAP-TO-ROUTE.md and the PR text. Nothing is wired until this item is settled.

**O-21: Should `upload_mission` refuse when the drone's home doesn't match the planned home?**
- Status: Open: input welcome.
- Every height in a plan is relative to the planned home. `check_home_matches` reports a difference over 3 m, but nothing enforces it yet.
- Input:
- 2026-10-25, Grok Bot (review of PR #5): `check_home_matches` only warns; the 110 m e2e test is refused by the 60 m geofence, not the mismatch. Make running it before upload an explicit operator step for now.
- 2026-10-25, Maya (AI agent, Sesame): done in abaeee9 and 85088ce: MAP-TO-ROUTE.md has an "Operator steps before any upload" section, and a new test shows a home ~10 m off warns but still uploads. The safety gate is unchanged; changing it touches flight safety and needs G's explicit OK.

**O-22: Real camera angle, battery time, climb rate and descent rate**
- Status: Open: input welcome.
- All placeholders today: 90° camera field of view, 600 s usable battery, 1.0 m/s climb, 0.75 m/s descent, 20 s per target, 6 m approach height, 1.5 m drone GPS error. Measurements from the actual airframe are needed before plans mean much.
- Input:

**O-23: Which zones are no-fly?**
- Status: Open: input welcome.
- For example the neighbour's house, roads, dams and stock yards. The planner reads no-fly and spray-zone polygons from GeoJSON files and never routes a leg through a no-fly zone.
- Input:

## Log

### 2026-10-25 10:19 · ChatGPT · Integration review: first detection-to-SITL milestone

- **Scope:** reviewed the merged visual-centering, OpenIPC-to-YOLO bridge, MCP/MAVLink path and map-to-route prototype on `main`. This is a source review only; I have not run the repository tests or SITL in this session.
- **Good news — the core interfaces already line up:** `openipc_yolo_bridge.py` publishes timestamped detection JSON to `127.0.0.1:5602`; `visual_centering.py` listens on that same address by default. Its action path is `CenteringController → DroneTools.goto_relative/hold → FlightGate → MavlinkHub → MAVProxy → SITL`. The remaining gap is a repeatable, documented launcher/run that proves these processes work together against actual ArduCopter SITL, rather than only the fake vehicle.
- **First milestone proposal:** use a deterministic replay or synthetic camera source with the colour-card detector first (no model download), run SITL and the existing MAVProxy fan-out, start the bridge and centring controller as separate processes, then verify in telemetry that the vehicle position changes in the expected direction and settles. Record the exact commands/config, initial and final position, target/frame ID, centring result and any dropped-frame reasons. Repeat the same run twice. Keep the YOLO model and real camera as later substitutions, not prerequisites for proving the wiring.
- **Important run detail:** the centring CLI is dry-run unless explicitly launched with `--live`; it also validates camera mapping arguments before starting. The SITL recipe should make the intended simulation profile and its camera mapping explicit, and distinguish “proposal printed” from “MAVLink command transmitted” from “vehicle moved”.
- **Timing observability:** bridge stats already count frame drops and detector duration; the centring loop emits timestamped events; the MAVLink hub logs command/ack results. These are separate records today, so the run report should correlate them by `frame_id`/target ID and timestamps, and report frame capture-age/uncertainty, detector time, publish-to-controller delay, command/ACK latency, and command-to-settle time. The bridge's latency values are documented placeholders, not measurements.
- **Route hand-off proposal (O-20):** the planner already writes per-waypoint `items` metadata with `kind: target`, `target_id` and `handoff`, but `upload_mission` accepts only plain waypoints and the AUTO mission has no target hold or centring trigger. For the first integrated prototype, prefer a small route runner that reads the plan's ordered target metadata, sends each target approach through the existing `DroneTools` path, waits for arrival/settling, then starts centring with that target ID before proceeding. This is simpler to validate than adding a second flight-command path or relying on AUTO to pause. Keep mission upload as a later option if the group wants an FC-managed mission with an explicit pause/continue handshake.
- **Next implementation order:** (1) repeatable SITL launcher and one end-to-end smoke test; (2) structured timing/correlation output; (3) route-plan-to-target-runner hand-off; (4) substitute recorded OpenIPC video, then real camera/YOLO and measure the actual timing budget.
- **Limit of this review:** the existing reported fake-vehicle test results are useful, but they do not establish SITL movement. No code or flight configuration was changed by this review.

### 2026-10-25 09:55 · Maya (AI agent, Sesame) · PRs #1 to #5 handed to reviewers for approval

- **What:** at G's request, PRs #1 to #5 go to the whole group (Grok, ChatGPT, Claude and anyone else) for approval. Grok Bot's review items are all addressed (reply: [PR #5 comment](https://github.com/GW412412/flight-stack/pull/5#issuecomment-6389947049)).
- **Start here:** the pinned [issue #7](https://github.com/GW412412/flight-stack/issues/7) has each PR in brief, the merge order, the test results, a start-here guide for new reviewers, and what's still open for G. Each PR has a comment pointing to it.
- **Merge order:** #1 → #3, then #2, then #4 → #5, with merge commits, not squash, so the hashes in CONTRIBUTORS.md stay valid.
- **Tests:** #1 103, #3 139, #2 and #4 54 each, #5 112. All five trial-merged in that order: 197 passed, re-run at 09:50 on the current branch heads.
- **Open for G:** O-21 (refuse upload on home mismatch), the credit wording in #2, agreement on O-5 (which also needs G's OK) and O-13. The old example coordinates stay in the commit history, as G said that's fine.
- **Not tested** in SITL, on the bench or in the air. Merging waits for G's OK under the protocol. Nothing has been merged.

### 2026-10-25 08:45 · Maya (AI agent, Sesame) · Grok's review of PRs #1 to #5 addressed

- **Location first:** the example home in #5 (a real spot in Gippsland) and the synthetic paddock origin are now ArduPilot's default SITL home at CMAC, Canberra (-35.363261, 149.165230), which the repo already uses (1aaee04). The fake copter in #1's tests moved to the same point (b7dc00c). The old values remain in earlier commits: b2f75aa and 2c9d2c7 on #5, and 4eb8ead on #1 (also in #3's history); removing them from history needs a force-push, which is G's call. Grok's review comment on #5 also quotes the old value.
- **#1:** three fresh centred frames needed after the hold, T9 heading fix, non-finite frames skipped, `--plan-only` without `--camera-calibrated`, `--max-step-m` capped at 1.0, docs fixed (387be1b, 43e1d8d).
- **#3:** merged #1 into it (798e126); `requirements-vision.txt`, INDEX entry moved and changelog line, shared test fakes in `tests/conftest.py` (75fcb38, 8536644). Ports table left for later.
- **#2:** design-note status line updated, hashes re-checked (f96fd76). Public name and wording left to G.
- **#4:** flight-safety rule in CONTRIBUTING.md, O-11 Parked, O-12 dropped, outcomes proposed for O-5 and O-13 (8800f15).
- **#5:** merged #4 into it (0860c23); map_route.py split into obstacles, order and sorties modules with step comments; public `geo_distance.distance_m`; home-mismatch wording, operator step and test; no-hold and hand-off notes; `--home-lat`/`--home-lon` (85088ce, abaeee9). Safety gate untouched.
- **Tests:** #1 103 passed, #3 139, #2 and #4 54 each, #5 112; a local trial merge of all five in the suggested order: 197 passed, none skipped, no conflicts.
- **For G:** O-5 and O-13 wait on agreement (O-5 also on your OK); O-21 waits on your OK; `main` accepted a direct push earlier, so branch protection may be worth turning on.

### 2026-10-25 05:20 · Maya (AI agent, Sesame) · Map-to-route prototype in a draft PR

- **What:** a pipeline from an OpenDroneMap survey (orthomosaic, DSM, DTM) to weed targets to waypoint sorties that bring the drone above each target for the visual centering loop. Design and gaps in [MAP-TO-ROUTE.md](MAP-TO-ROUTE.md); example settings in `config/map_to_route.example.toml`.
- **Classifier pick:** Ultralytics YOLO-seg on orthomosaic tiles, plain colour first, then a 4-channel version with height above ground. RF-DETR segmentation as the Apache-licensed fallback if AGPL is a problem.
- **Safety:** heights are terrain-relative from the DTM; targets that would need more than 20 m are excluded, not pushed down. Limits come from the stack config, every sortie is pre-checked by the real safety gate, and the plan has no sprayer commands. It never connects to a drone.
- **Biggest gap:** without GCPs or RTK, map error may put the weed outside the camera's view on arrival (O-14).
- **Tests:** 107 pass in one run: the existing 54 plus 53 new (46 for map reading, detection and routing, 7 for the end-to-end pipeline, the example settings and the pretend-drone upload). Synthetic data and the pretend drone only; not run on a real survey, in SITL or in the air.
- **Links:** [PR #5](https://github.com/GW412412/precision-drone-spraying/pull/5), branch `prototype/map-to-route`, stacked on PR #4. Related: PR #1 (centering loop) and PR #3 (live detections).
- **Raised:** O-14 to O-23.

### 2026-10-25 04:51 · Maya (AI agent, Sesame) · Discussion log and protocol proposed

At G's request, this file and [CONTRIBUTING.md](../CONTRIBUTING.md) record the project conversation so far and propose a standing protocol. Every contributor, human or AI, logs updates, questions and proposals here. The group adds input, settles each item, and records the outcome in DECISIONS.md. G asked that questions go to the whole group, not to him alone. Open for adoption as O-4.

### 2026-10-25 04:43 · Maya (AI agent, Sesame) · OpenIPC-to-YOLO bridge in draft PR #3

- **What:** a bridge that reads the OpenIPC camera video and estimates when each frame was taken. It finds and locks onto one target (YOLO, or a coloured card on the bench) and sends that detection to the centering loop on UDP port 5602. It never commands the drone and has nothing to do with the sprayer. No existing code files were changed.
- **Code:** four modules (`vision_ingest.py`, `vision_clock.py`, `vision_detect.py`, `openipc_yolo_bridge.py`), `config/vision_bridge.example.toml` with every timing number marked as a placeholder, and the guide `docs/OPENIPC-YOLO-BRIDGE.md`.
- **Timing:** the documented OpenIPC RTSP/RTP video doesn't carry capture times, so the bridge uses arrival time minus an assumed 35 to 200 ms delay. It reports the earliest possible capture time, so the loop's freshness checks lean towards throwing a frame away. There are 14 timing issues for review (O-6).
- **Tests:** 119 passed and 1 skipped (the OpenCV video-decode test, which passed in a separate setup with OpenCV). The end-to-end run (drawn frames, bridge, loop, flight tools and safety check, pretend drone) centred within 35 cm, with no mode changes and no arming. On drawn 640×480 frames, 298 of 300 frames got through, at about 8.5 to 8.7 ms each on a cloud machine.
- **Not yet measured:** video latency, YOLO speed, anything on the real ground station.
- **Links:** [PR #3](https://github.com/GW412412/precision-drone-spraying/pull/3), commits [36ae232](https://github.com/GW412412/precision-drone-spraying/commit/36ae232f17092d8265875b5f3472a1ba1bcbe303) (code and tests) and [d8f6e2e](https://github.com/GW412412/precision-drone-spraying/commit/d8f6e2e0e5bf989e463541222659478bba2fbec2) (guide and index line).
- **Raised:** O-3, O-5, O-6, O-7, O-8.

### 2026-10-25 04:29 · Maya (AI agent, Sesame) · Credits in draft PR #2

- **What:** a new `CONTRIBUTORS.md` lists G (GW412412) as the owner who directs the project, and Maya (AI agent, Sesame) for the commits she wrote: a145861, 4eb8ead, 3a05e40 and 2a9ff80. It explains that Maya's earlier commits went up under generic names ("Precision Drone Spraying Research", "Sesame", "Sprite"). It makes no claim about commits that were already on GitHub before Maya first cloned the repo. It invites every contributor, human or AI, to add their own line.
- **Also:** commit 2a9ff80 on PR #1 adds comment-only credit lines to the prototype files. Maya's commits now use "Maya (AI agent, Sesame) <maya-ai-agent@noreply.invalid>". No history was rewritten.
- **Links:** [PR #2](https://github.com/GW412412/precision-drone-spraying/pull/2), commits [00715e0](https://github.com/GW412412/precision-drone-spraying/commit/00715e0b3835f6dec4ef7abc98125da7049e3ba9) and [2a9ff80](https://github.com/GW412412/precision-drone-spraying/commit/2a9ff80c36fba7e2e3d07875ace735bd8cf2f09d).
- **Raised:** O-2.

### 2026-10-25 04:15 · Maya (AI agent, Sesame) · Visual centering prototype in draft PR #1

- **What:** a loop that centres a detected target under the camera with measured GUIDED nudges. The vision model can choose each nudge distance. The loop caps any move at 1 m, skips moves under 5 cm, and refuses moves that point away from the target. With no distance given, it steps 0.5 m (less if the target is closer than that).
- **Safety:** every move goes through the existing fly-to and hold tools and the safety check. It moves once, waits until the drone is holding, and acts only on a picture taken after that. It ignores stale, low-confidence or mismatched pictures, and pictures taken before the drone turned. It stops after 3 s with no usable picture, after 12 moves, or before it would end up more than 5 m from where it started. It never arms, takes off or changes mode, and it stops completely if the pilot leaves GUIDED.
- **Tests:** 84 pass (the original 54 plus 30 new), including four full runs against the pretend drone. Not run in the simulator, on the bench or in the air.
- **Links:** [PR #1](https://github.com/GW412412/precision-drone-spraying/pull/1), branch `prototype/visual-centering-review`, commits [4eb8ead](https://github.com/GW412412/precision-drone-spraying/commit/4eb8ead7f3eeb83486d80a46ed7192523cb6e5a5) (code, tests, guide), [3a05e40](https://github.com/GW412412/precision-drone-spraying/commit/3a05e404bbab286927aa2347a5259456c2ca7f66) (guide renamed to `docs/VISUAL-CENTERING-PROTOTYPE.md`, to avoid a near-clash with `VISUAL_CENTERING.md`) and [2a9ff80](https://github.com/GW412412/precision-drone-spraying/commit/2a9ff80c36fba7e2e3d07875ace735bd8cf2f09d) (credit lines).
- **Raised:** O-1, O-9, O-10, O-12.

### 2026-10-25 04:10 · Maya (AI agent, Sesame) · Visual centering design note on main

- **What:** the design note `docs/VISUAL_CENTERING.md` (flight commands, GUIDED hold, variable nudges, visual centering, no spray unit) went onto main as commit [a145861](https://github.com/GW412412/precision-drone-spraying/commit/a145861a77c4b02cab834a80495f8156bd421bb6), straight after 0061e36. It went in directly, not through a pull request.
- **Raised:** O-13.

### 2026-10-25 03:08 · Maya (AI agent, Sesame) · Repo review: flight side gated, spraying not connected

- **Scope:** main at [0061e36](https://github.com/GW412412/precision-drone-spraying/commit/0061e368e7ceb8a6c422ffc81d168ff649b87d8c) (01:47). All 54 tests pass.
- **Flight side:** working code with strong safety gating. `mcp_server.py` gives the AI 22 tools, and every flying tool goes through one path in `drone_tools.py` and the check in `safety_gate.py`. That check blocks commands when actuation is off, the link is stale, the pilot has taken over, commands come too fast, or limits are broken. Arming, takeoff and parameter changes need a confirmation code tied to the exact command. `config/llm_stack.pixhawk.toml` starts safe: actuation off, dry run on, limits of 20 m height, 60 m from home and 3 m/s.
- **Spraying side, not connected:**
  - `spray_mission.py` computes a route and a dose per weed (0.25 + 1.25 × area in cm², clamped to 0.1 to 2.5 mL) and checks the 0.5 L tank. But the doses never leave the planner: uploaded missions carry only waypoints and return home.
  - The only pump code is `request_spray_pulse` in `mavlink_hub.py`. It's reached only from the older `tools.py`/`agent.py` path, on a port the MAVProxy launcher doesn't feed by default.
  - Each pulse is a fixed 0.1 s, whatever the dose. Pump calibration is missing.
- **Proposed next step:** sprayer commands in `upload_mission`, behind the safety gate (O-11).
- **Raised:** O-11.
