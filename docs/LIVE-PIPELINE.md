# Live camera-to-nozzle weed-ID pipeline

Concrete **ground-station** inference path from the Thinker / IMX335 video
stream to a MAVLink spray command. Complements offline orthomosaic ID in
[WEED-ID.md](WEED-ID.md) and [LABELING-WORKFLOW.md](LABELING-WORKFLOW.md).
Hardware and RF are locked in [SYSTEM-SPEC.md](SYSTEM-SPEC.md); sprayer MAVLink
in [STACK.md](STACK.md).

**Authority:** ELRS remains hard flight authority. Live ID may enable the pump
only when gates below pass. Video RF never carries RC or spray commands.

Compiled 2026-09-26. Decisions in this file are **locked for v1** unless
explicitly revised.

---

## Operational context (touch-up paddocks)

This live pipeline is for **touch-up spraying on otherwise clean paddocks**, not
blanket or dense-infestation work.

| Fact | Design implication |
|------|--------------------|
| Typical load | **~25 weeds** scattered across a **~5-acre (~2 ha)** paddock |
| Pattern | Peppered **localised patches**, long stretches of clean pasture between |
| Chemical | **0.5 L tank** with **higher-concentration** mix (active ingredient per weed ≈ former 1 L standard-strength plan; carrier volume halved); dose conservatism is still **stewardship** |
| Detection priority | **High precision over high recall** — a false positive wastes chemical and pilot time on a clean paddock |
| Offline review | Any **missed weed** on a clean-paddock spot-check is a **failed pass** (goal is **zero escapes**) |
| Routing | Waypoint optimiser must **cluster** nearby targets into short efficient passes, not long zig-zag traverses across empty ground ([WAYPOINT-OPT.md](WAYPOINT-OPT.md)) |

Live auto-spray therefore locks **`conf ≥ 0.50`**. Misses that slip the live gate
are recovered by a second **clustered waypoint** pass from the offline map, not
by lowering confidence until every grass clump triggers.

---

## 0. Where it runs

| Piece | Location |
|-------|----------|
| Camera + encode | Air: Thinker Base + IMX335 → H.264 720p30 |
| Video RF | 5 GHz WFB-NG (RTL8812AU) |
| Decode + weed ID + spray decision | **Ground station only** (40-core CPU / 256 GB / optional Tesla K80) |
| Spray actuator | Pixhawk `AC_Sprayer` / section relays via **telemetry MAVLink** |
| Model weights | Same `YOLOv8n-seg` ONNX as offline (`data/models/.../best.onnx`) |

On-drone companion inference is **out of scope for v1**.

---

## 1. Capture and preprocess (GS)

### 1.1 Ingest

```
WFB-NG decode → local RTP/UDP :5600 or RTSP restream
             → OpenCV / GStreamer / FFmpeg frame grab
             → timestamped BGR frame + frame_id
```

- Prefer **main stream** 1280×720 @ 30 fps (SYSTEM-SPEC).
- Associate each frame with the **nearest MAVLink `GLOBAL_POSITION_INT` /
  `ATTITUDE` / `GPS_RAW_INT` (or RTK equivalent)** by time, using a ring buffer
  of telem samples (≤ 50 ms age). Drop the frame if telem age > **80 ms**.
- Process **every Nth frame** so inference stays inside budget: default
  **N = 2** (15 Hz ID) on CPU; **N = 1** only if measured decode+ID ≤ 40 ms.

### 1.2 ROI extraction

At spray AGL (~5–15 m) the boom footprint is a strip under the aircraft, not
the full horizon.

1. Using current attitude, AGL (downward rangefinder or baro+terrain), and
   calibrated camera extrinsics, project a **ground rectangle** covering
   **boom width × look-ahead distance**.
2. Look-ahead default: **2.0 s × ground speed** (clamp 4–12 m).
3. Crop that rectangle from the 720p frame → ROI. If projection fails
   (bad attitude, AGL unknown), fall back to a **fixed central crop**:
   centre **640×640** of the 720p frame (letterbox height).

### 1.3 Resize and normalisation

| Step | Locked value |
|------|----------------|
| Resize | ROI → **640×640** (`imgsz=640`), letterbox preserve aspect, pad with 114 |
| Colour | BGR → RGB |
| Dtype | `float32`, scale **÷ 255** → \[0, 1\] |
| Layout | NCHW batch=1 for ONNX Runtime |
| Optional | Skip CLAHE/live colour jitter (train-time only) |

No extra ImageNet mean/std: Ultralytics YOLO-seg export expects **0–1**
unless the ONNX metadata says otherwise — match the export used in labeling.

---

## 2. Inference (YOLOv8n-seg ONNX)

| Setting | Locked |
|---------|--------|
| Model | **YOLOv8n-seg** ONNX (nano segmentation) |
| Runtime | **ONNX Runtime CPU** (40 threads max); **K80 CUDA EP only** if a verified CUDA 11 + sm_37 stack is already working on the GS |
| `imgsz` | **640** |
| `conf` | **0.50** (precision-first live gate; offline pre-label stays 0.25) |
| `iou` | **0.5** (NMS) |
| Max det | **20** per ROI (sparse touch-up; was 50 for dense) |
| Classes | DeepWeeds-aligned IDs 0–7; treat class 8 / background as non-spray. If ops collapsed labels to single `rosette`, spray any weed class ≥ conf |

**Outputs used:** instance mask (or polygon), box, class id, confidence,
mask area in pixels.

**Post-NMS:** discard masks with area < **40 px** at 640 (noise); require
mask∩box IoU sanity if both exist.

---

## 3. Georegistration (pixel → world)

For each surviving detection at frame time \(t\):

### 3.1 Inputs (must be co-timed)

- Drone **WGS84** position (prefer RTK float/fixed; else GPS)
- **Attitude** (roll, pitch, yaw) from FC
- **AGL** \(h\) from downward rangefinder when in range; else baro − terrain
- **Camera intrinsics** \(K\) (calibrated once on the IMX335+lens; store in
  `config/camera-intrinsics.yaml`)
- **Extrinsics:** camera-to-body rotation/translation; **gimbal angle** if
  gimballed (v1 default: **fixed nadir / slight forward** mount — gimbal
  angle = mount constant)
- Pixel centroid of the mask (or box centre if mask missing)

### 3.2 Projection (v1)

1. Undistort pixel \((u,v)\) with \(K\) and distortion coeffs.
2. Ray in camera frame → body → NED using attitude and mount.
3. Intersect ray with local **tangent ground plane** at height \(h\) below
   the camera (flat-earth under the boom for v1; DEM optional later).
4. Convert NED offset to **WGS84 lat/lon** (and store local ENU metres for
   boom mapping).

### 3.3 Georegistration confidence \(g\)

Start at **1.0**, multiply penalties (clamp to \[0, 1\]):

| Condition | Multiply \(g\) by |
|-----------|-------------------|
| RTK fixed | 1.0 |
| RTK float / good GPS (hdop ≤ 1.5) | 0.85 |
| GPS only, hdop > 1.5 or sats < 10 | 0.5 |
| Telem age 40–80 ms | 0.8 |
| AGL from baro only (no rangefinder) | 0.75 |
| \(|roll|\) or \(|pitch|\) > 20° | 0.6 |
| Fallback central crop (no projected ROI) | 0.7 |

**Accept georeg** only if \(g \ge \mathbf{0.70}\). Below that: log detection,
**do not auto-spray** from that instance (operator may still spray manually).

Also reject if projected ground point is **> 1.5× boom half-width** laterally
outside the boom envelope (detection not under sprayable path).

---

## 4. Spray-decision rule

### 4.1 Gates (all must pass)

1. Live ID enabled by operator (RC aux or GS toggle).
2. Video link healthy (not in SYSTEM-SPEC freeze / stills-only mode).
3. Detection `conf ≥ 0.50` (touch-up precision gate).
4. Georeg \(g \ge 0.70\).
5. Vehicle armed, not in failsafe / RTL / Land (unless ops explicitly allow).
6. Ground speed ≥ `SPRAY_SPEED_MIN` (FC param) **or** use timed pulse mode
   below when hovering over a target (see duration).
7. No active **spray cooldown** overlap for the same world cell (see 4.4).

### 4.2 Dose from canopy area

**Tank:** **0.5 L** (half-litre) with a **higher-concentration** mix so that
**active ingredient per weed stays equivalent** to the former 1 L
standard-strength plan while **carrier volume per pulse is halved**.

Mask area in ROI pixels → ground area using GSD at AGL. Work in **cm²**:

\[
A_{\mathrm{cm}^2} = 10^{4}\, N_{\mathrm{px}} \cdot \left(\frac{h \cdot s_{\mathrm{px}}}{f}\right)^2
\]

where \(s_{\mathrm{px}}\) is sensor pixel pitch referred through the 640 letterbox
scale, \(f\) focal length (m), \(h\) AGL (m). Equivalently: store
`meters_per_pixel_at_1m` in intrinsics YAML, compute \(A_{\mathrm{m}^2}\), then
\(A_{\mathrm{cm}^2} = A_{\mathrm{m}^2} \times 10^{4}\).

**Locked dose function (v1.1, half volume / concentrate mix):**

\[
V_{\mathrm{mL}} = \mathrm{clamp}\bigl(V_0 + k \cdot A_{\mathrm{cm}^2},\; V_{\min},\; V_{\max}\bigr)
\]

| Symbol | Value | Meaning |
|--------|-------|---------|
| \(V_0\) | **0.25 mL** | Base offset per accepted weed (was 0.5) |
| \(k\) | **1.25 mL / cm²** | Linear canopy term (was 2.5) |
| \(V_{\min}\) | **0.1 mL** | Hard floor for any fired pulse (was 0.2) |
| \(V_{\max}\) | **2.5 mL** | Hard cap per detection (was 5) |
| \(A_{\min}\) | **0.5 cm²** | Below → skip (too small / noise) |

If \(A_{\mathrm{cm}^2} < A_{\min}\), **skip** (no command). Otherwise apply the
clamp above. Example: a **2 cm²** rosette → \(0.25 + 1.25\times 2 = 2.5\) mL
(hits cap); a **0.2 cm²** speck is skipped. At ~25 weeds, full 2.5 mL pulses
use only ~62 mL of the **0.5 L** tank — endurance is still not the driver;
smaller pulses cut drift and pasture burn with concentrate mix.

**Concentrate caveat:** mix to **label rates** for the product in use (do not
guess “2× strength”). Many herbicides **do not store well pre-mixed** — mix
**fresh for each flight**, discard leftover mix per label, and never leave
concentrate solution sitting in the tank or lines between days.

**Pump on-time** from calibrated flow \(Q\) (mL/s) at current `SPRAY_PUMP_*`
operating point (re-bench after switching to concentrate — flow vs PWM may
differ with viscosity):

\[
t_{\mathrm{on}} = \mathrm{clamp}\left(\frac{V}{Q},\; 0.04\,\mathrm{s},\; 0.40\,\mathrm{s}\right)
\]

### 4.3 Boom section / nozzle mapping

Assume a **linear boom** of \(S\) sections (default **S = 3**: left / centre /
right), width \(W\) metres, centred under the aircraft.

1. Transform detection ENU into **body lateral** axis \(y\) (right positive).
2. Section index:
   \[
   s = \mathrm{clamp}\left(\left\lfloor \frac{y + W/2}{W/S} \right\rfloor,\; 0,\; S-1\right)
   \]
3. **v1 actuation:**
   - If only a **single pump** (`AC_Sprayer`): fire the pump for \(t_{\mathrm{on}}\)
     whenever any section accepts (section id logged for later multi-valve).
   - If **per-section relays** exist: `DO_SET_RELAY` / servo for section \(s\)
     for \(t_{\mathrm{on}}\`; other sections off.

Nearest-nozzle refinement (optional): within a section, pick nozzle index by
finer \(y\) bins; v1 logs it but does not require >3 valves.

### 4.4 Dedup / cooldown

Hash world cell at **0.5 m** resolution. After an accept, **suppress** new
auto-spray for that cell for **3.0 s** (or until the aircraft has moved
**> 1.0 m** from the cell centre). Prevents 15 Hz re-triggers on one plant.

### 4.5 Sparse routing (waypoint / mission mode)

When spraying from a **precomputed map** (offline GeoJSON → mission) rather than
pure opportunistic live pulses, feed [WAYPOINT-OPT.md](WAYPOINT-OPT.md) with
**cluster-aware** ordering:

1. DBSCAN / radius cluster (~**15–25 m**) so a localised patch is one short pass.
2. Solve ATSP **within** each cluster, then order clusters (TSP on centroids).
3. Prefer prize-collecting only if time-limited; at ~25 weeds / 5 acres the
   default is **visit all** clusters — empty pasture is skipped by construction.

Live opportunistic mode still pulses when a gated detection is under the boom;
clustering mainly shapes the **scout → map → revisit** mission. On each mapped
visit, **approach-scan** (§4.6) turns the inbound leg into a mini
search-and-destroy pass so year-on-year weed movement near \(W\) is caught
without a global replan ([WAYPOINT-OPT.md](WAYPOINT-OPT.md) §7).

### 4.6 Approach-scan (search-and-destroy on mapped waypoints)

**Intent:** do **not** only fly straight to a known weed waypoint and spray the
mapped target. Keep the camera / live ID **actively scanning** the FOV on
approach for additional weed signatures. The offline map gets you **close**;
live scan catches what **shifted** since the scout (year-on-year movement,
late emergents, ortho/GPS drift).

#### 4.6.1 When it arms

Approach-scan is active when **all** of:

1. Mission / Guided is flying toward a **mapped** weed waypoint \(W\) (from
   the cluster ATSP mission).
2. Live ID is operator-enabled (same RC aux / GS toggle as §4.1).
3. Aircraft is inside the **approach corridor**: roughly from
   look-ahead distance out to \(W\), within ~**8–12 m** horizontal of the
   planned inbound track (same accept radius as WAYPOINT-OPT §7.3).

Outside a mapped approach, behaviour stays pure opportunistic boom pulses
(§4.1–4.4) with no path divert.

#### 4.6.2 Widen the scan window

While approach-scan is armed, slightly **widen** the §1.2 ROI before
inference:

| Parameter | Cruise / opportunistic | Approach-scan |
|-----------|------------------------|---------------|
| Look-ahead | **2.0 s × GS** (clamp 4–12 m) | **2.5 s × GS** (clamp 5–15 m) |
| Lateral boom envelope | boom width | boom width **× 1.25** (still reject sprays outside true boom in §3.3) |
| `conf` / georeg / cooldown | unchanged | **same locks**: conf ≥ **0.50**, \(g \ge 0.70\), 0.5 m / 3 s cooldown, RC inhibit, fail-closed |

Widening is for **detection discovery** only. Auto-spray still requires the
projected point under the sprayable boom (or a brief hover/divert that puts
it there). Do not lower conf to “find more” — precision-first stays locked.

#### 4.6.3 New detection → active spray list

On each accepted frame during approach:

1. Run normal gates (§4.1): conf ≥ 0.50, \(g \ge 0.70\), link/telem/RC/failsafe.
2. If the detection’s world cell matches the **mapped** \(W\) (within cooldown
   cell / ~0.5–1 m): treat as the planned target (origin=`mapped`).
3. Else if within approach accept radius of \(W\) and not on cooldown: treat as
   **opportunistic** — georegister, append to the **active spray list** for
   this approach (cap **3** inserts per \(W\)).
4. Detections outside the corridor: log only (or boom-pulse if already under
   boom in opportunistic mode); do **not** divert the approach path.

Active spray list is seeded with mapped \(W\) when the approach arms.

#### 4.6.4 Path adjust / hover via `mavlink_hub`

Only `mavlink_hub` may write flight or spray commands ([INTEGRATION.md](INTEGRATION.md)).

Live ID publishes events; hub executes if RC and fail-closed rules allow:

| Event | Hub action |
|-------|------------|
| `SprayRequest` (mapped or opportunistic) | Existing spray arbiter → `MAV_CMD_DO_SPRAYER` |
| `HoverRequest` (brief) | Short **Loiter** or position-hold (order of **2–4 s**) while a fringe target is engaged |
| `PathOffsetRequest` | Small **Guided** lateral/forward offset (metres) to put boom over a list member, then resume toward remaining list / next WP |

v1 keeps adjusts **local and brief**. Hub **drops** hover/offset if: RC
sprayer inhibit, mode conflict with pilot stick, failsafe/RTL/Land, OA
fighting the setpoint, or video/georeg untrusted. No second spray writer.

#### 4.6.5 Engage / spray order (simple rule)

**Nearest-first on the active spray list:**

1. While the list is non-empty and approach-scan is still armed for \(W\):
   pick the pending target **closest** to the current aircraft ground
   position (ENU).
2. If that target is under the boom → dose + pulse (§4.2–4.3).
3. If not → hub `PathOffsetRequest` / brief `HoverRequest`, then pulse when
   gated under boom.
4. Mark sprayed (cooldown cell); remove from list; **recompute** nearest
   among remaining (includes mapped \(W\) until sprayed).
5. When the list is empty (or insert cap hit and \(W\) sprayed) → clear
   approach-scan for this \(W\) and continue the **precomputed** next ATSP
   edge. **No global replan.**

So new weeds found on the way in are engaged **before or interleaved with**
\(W\) by geometry, not by a complex TSP insert. Mapped \(W\) is never
skipped unless gates fail (then log and move on — offline revisit later).

#### 4.6.6 Logging (mapped vs opportunistic)

Extend `logs/live-pipeline.jsonl` accepts with:

| Field | Values / meaning |
|-------|------------------|
| `origin` | `mapped` \| `opportunistic` |
| `approach_wp_id` | Mission / GeoJSON id of \(W\) being approached |
| `insert_index` | 0 for mapped seed; 1…n for live inserts |
| `path_adjust` | `none` \| `hover` \| `offset` (what hub ran before pulse) |
| `year_shift_hint` | Optional: distance (m) from mapped \(W\) for opportunistic hits — post-flight signal that the map aged |

Hub mirrors spray/mode lines in `logs/mavlink-commands.jsonl` as today.
Post-flight review: opportunistic fraction near mapped waypoints is the
metric that approach-scan is working (map close, live catch of movers).

---

## 5. MAVLink command path and latency budget

### 5.1 Transport

- Send spray commands on the **FC telemetry link** (SiK / ELRS telem /
  Ethernet companion bridge) — **never** on WFB-NG video RF.
- Primary: **`MAV_CMD_DO_SPRAYER` (216)** — param1 = **1** start, **0** stop.
- Alternate (if 216 unavailable on build): **`MAV_CMD_DO_AUX_FUNCTION`** with
  aux **15** (Sprayer), switch position high to enable, low to disable.
- Timed pulse: GS starts spray (param1=1), sleeps \(t_{\mathrm{on}}\) on GS clock,
  sends stop (param1=0). Prefer a short Lua helper on the FC later; v1 is
  GS-timed.

### 5.2 Latency budget (locked, total ≤ **150 ms** decision loop)

| Segment | Budget |
|---------|--------|
| Capture + H.264 encode (air) | 25 ms |
| WFB-NG air → GS | 30 ms |
| Decode + ROI + resize | 15 ms |
| ONNX YOLOv8n-seg infer (CPU, 640) | **40 ms** |
| Georeg + dose + section map | 5 ms |
| MAVLink spray command RTT start | **20 ms** |
| **Total aim** | **≤ 135 ms** (margin to **150 ms**) |

If measured decode+ID > 40 ms: increase frame skip \(N\), or shrink ROI, or
enable K80 EP — **do not** raise conf or skip georeg to “go faster”.

Log `t_frame_capture`, `t_infer_done`, `t_mav_sent` per accept for post-flight
review (`logs/live-pipeline.jsonl`).

---

## 6. Fallback behaviour

| Condition | Action |
|-----------|--------|
| Georeg \(g < 0.70\) | No auto-spray; log detection; optional HUD warn |
| Telem age > 80 ms | Drop frame; no command |
| Video link degraded (SYSTEM-SPEC steps 1–3) | Raise live conf gate to **0.60**; keep spray if still gated |
| Video step 4 (stills / freeze live ID) or RF loss | **Pump off immediately**; disable live auto-spray; continue ELRS; spray only from **precomputed waypoint mission** if already uploaded |
| Infer timeout (> 2× budget) | Skip frame; after 5 consecutive skips → disable live auto-spray and speak/log `Live ID stalled` |
| Operator RC sprayer switch off | Hard inhibit — GS must not override |
| Failsafe / RTL / Land | Force `DO_SPRAYER` off; refuse new enables |

**Fail closed:** never leave the pump on when video or georeg is untrusted.

---

## 7. Offline acceptance bar before live ops

Live auto-spray is **authorised only after** the same weights pass offline
eval on a **held-out clean paddock** tile set (not used in training):

| Metric | Minimum |
|--------|---------|
| **mAP50** (instance seg, weed classes) | **≥ 0.80** |
| **Recall** (instance, at **conf 0.50**) | **≥ 0.85** on the held-out set |
| False-positive rate on bare-pasture tiles | **≤ 2%** of tiles with any FP at **conf 0.50** (tighter: clean paddocks punish FPs) |
| **10% miss spot-check** (human) | **Zero escapes** — any missed weed in the sampled clean-paddock tiles is a **failed pass**; retrain / relabel before live ops |

The spot-check rule overrides a “good enough” average recall: on otherwise clean
paddocks the operational goal is **no escapes**, so one miss in the 10% sample
fails the model for live authorisation (same spirit as LABELING-WORKFLOW review,
stricter here).

Record the eval run (date, commit hash, `data.yaml`, metrics) under
`data/models/<name>/EVAL.md`. Until that file exists with passing numbers,
live ID may run in **shadow mode** (detect + log only, pump commands
inhibited).

---

## 8. End-to-end diagram

```
IMX335 → Thinker H.264 720p30 → WFB-NG 5 GHz
      → GS decode (RTP/RTSP)
      → telem-synced frame
      → boom ROI → letterbox 640 → /255 NCHW
      → YOLOv8n-seg ONNX (conf=0.50, iou=0.5)
      → NMS / area filter
      → georeg (GPS + attitude + K + mount + AGL) → g
      → if conf & g gates: dose V(A) → t_on → section s
      → on approach to mapped W: widen ROI; active spray list
        (mapped + opportunistic, nearest-first; hover/offset via hub)
      → MAV_CMD_DO_SPRAYER (telem) → AC_Sprayer / relays
      → JSONL audit log (origin=mapped|opportunistic)
```

---

## 9. One-paragraph summary

Live weed spray for this project is **touch-up on clean paddocks** (~**25 weeds
/ 5 acres**, localised patches): it runs **entirely on the ground station**,
decodes the Thinker/IMX335 WFB-NG stream, crops a boom ROI, letterboxes to
**640**, runs **YOLOv8n-seg ONNX** at **conf 0.50 / iou 0.5** (precision over
recall), projects each mask to WGS84 with GPS/attitude/intrinsics/AGL (georeg
≥ 0.70), doses \(V = \mathrm{clamp}(0.25 + 1.25\,A_{\mathrm{cm}^2},\, 0.1,\, 2.5)\) mL from a
**0.5 L** concentrate tank (AI-equivalent, half carrier), pulses the nearest of
**3 boom sections** via **`MAV_CMD_DO_SPRAYER` on telemetry** inside **≤ 150 ms**,
clusters sparse waypoint revisits, runs **approach-scan** on each mapped weed
(widened FOV, nearest-first active spray list, brief hover/offset via
`mavlink_hub`) so year-on-year movers near the map are caught, fails closed on
bad georeg or link loss, and authorises live ops only after offline
**mAP50 ≥ 0.80**, **recall ≥ 0.85** at conf 0.50, and a **zero-escape** 10% miss
spot-check on a held-out clean paddock.

---

## Related docs

- [SYSTEM-SPEC.md](SYSTEM-SPEC.md) — camera, RF, latency, link fallbacks
- [WEED-ID.md](WEED-ID.md) — offline ortho tiling / model family
- [LABELING-WORKFLOW.md](LABELING-WORKFLOW.md) — train / export ONNX
- [STACK.md](STACK.md) — `AC_Sprayer`, MAVLink 216 / aux 15
- [WAYPOINT-OPT.md](WAYPOINT-OPT.md) — clustered ATSP mission + approach-scan planner hooks (§7)
- [INTEGRATION.md](INTEGRATION.md) — `mavlink_hub` spray/hover arbiter, RC authority
