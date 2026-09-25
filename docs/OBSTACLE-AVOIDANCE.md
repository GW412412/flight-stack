# Obstacle / Collision Avoidance — Pixhawk Spot-Spray (Hill Country)

Research notes for a **compact Pixhawk / ArduCopter** spot-spraying multirotor flying **Guided / Auto waypoint missions at low altitude** over **hills, trees, and fences**. Prefer sensors the flight controller (FC) can talk to directly (UART / I2C / CAN). Companion computers are optional, not the default.

> Status: local research notes. Not vendor advice. Tune margins and speeds on your airframe; OA is **not** a substitute for good mission planning and an RC kill / disable switch.

---

## Voice-friendly recommendation (5–8 sentences)

For a compact Pixhawk sprayer on Guided waypoints over hills, keep obstacle avoidance on the **flight controller** and skip a companion computer unless you later need depth cameras or onboard weed ID. Fit a **downward micro-LiDAR** (e.g. Benewake TFmini Plus) for surface tracking and low-altitude AGL control, plus a **lightweight horizontal proximity** setup: either one **LightWare SF45/B** (~50 g, serial, native PRX) or two–three **forward/side TFmini-class rangefinders** with `PRX1_TYPE = 4`. Enable **Simple Avoidance** (`AVOID_ENABLE`) for Loiter/pilot modes, and for Guided/Auto set **`OA_TYPE = 1` (BendyRuler)** so proximity hits bend the path; use **`OA_TYPE = 3`** only if you also rely on polygon fences for known tree clumps. Optical flow helps GPS-weak hover but is **not** obstacle avoidance; ultrasonics are too short-range and noisy outdoors for trees and fences. Stereo vision and most mmWave point clouds need a companion (Pi may struggle; Jetson is the practical choice) and add weight/power you do not need for a minimal stack. Keep LOOKAHEAD/MARGIN conservative, set an RC option to disable avoidance mid-flight, and treat hills primarily with a **downward rangefinder + terrain/surface tracking**, not horizontal OA alone.

---

## 1. Sensors — pros / cons for a small airframe over hills

Hill-country threats: **terrain rise**, **tree crowns / trunks**, **fence posts and wire**, dust/spray mist, and low-sun glare. Horizontal OA and vertical AGL sensing are different jobs — plan both.

| Sensor class | Role | Pros (compact / hills) | Cons | FC direct? |
|---|---|---|---|---|
| **Scanning LiDAR** (LightWare SF45/B ~350°, SF40/C 360°, RPLidar A2/C1, TeraRanger Tower) | Horizontal proximity → Simple Avoidance + BendyRuler | Wide FOV; official PRX drivers; SF45/B is relatively light (~50 g) and serial | Weight/cost (SF40/C ~250 g+); spinning units dislike mounting clutter; outdoor sunlight/weather varies by model; can see **ground as obstacles** at low AGL | Yes — UART `SERIALx_PROTOCOL = 11` (Lidar360), `PRXx_TYPE` per model |
| **Unidirectional micro-LiDAR** (Benewake TFmini / Plus, LW20, etc.) | Down AGL + 1–8 horizontal sectors as rangefinders | Tiny/light (≈5–11 g); UART or I2C; cheap redundancy (forward + sides); good for **surface tracking** | Narrow beam misses thin posts/wire; outdoor range often **≤6–7 m**; rain/dust/spray mist degrade ToF IR | Yes — `RNGFNDx_TYPE` (e.g. 20 serial / 25 I2C for TFmini); set `PRXx_TYPE = 4` to use as proximity |
| **Ultrasonic** (Maxbotix, HC-SR04, GY-US42) | Very short-range bump / soft stop | Cheap; works in dark; some I2C/analog FC support | **Short range**; outdoor wind/prop wash noise; soft vegetation and thin fences unreliable; poor primary OA for trees | Yes — as rangefinders; not preferred outdoors |
| **Optical flow** (HereFlow, PX4Flow-class, etc.) | EKF velocity / GPS-denied Loiter — **not OA** | Helps low, slow position hold over textured pasture | Needs downward rangefinder in range; useless as obstacle detector; fails over uniform grass/water | Yes — FLOW_* + EKF sources; pair with down LiDAR |
| **mmWave radar** (Hexsoon 77G, Nanoradar MR72/NRA24, Hexsoon 24G, community TI IWR6843) | Proximity / altimeter through dust/fog | Better through spray mist than optical ToF; some units now on ArduPilot rangefinder/proximity lists | FOV/clutter: **ground returns at low AGL** need filtering; many 3D radars still need companion → MAVLink; weight/cost | Mixed — some native (see wiki lists); complex 3D often companion |
| **Stereo / depth vision** (Intel RealSense, OpenKai + ZED, etc.) | Rich 3D obstacles via `OBSTACLE_DISTANCE` / `_3D` | Dense FOV; can feed PRX over MAVLink | **Needs companion CPU**; sunlight/texture failures; weight, heat, cabling; more failure modes | No — companion → MAVLink (`PRXx_TYPE = 2` MAVLink) |

**Hill-country specifics**

- **Terrain** is primarily a **downward rangefinder + Surface Tracking / Terrain Following** problem, not horizontal BendyRuler. See [Surface Tracking](https://ardupilot.org/copter/docs/terrain-following-manual-modes.html) and [Terrain Following (Auto)](https://ardupilot.org/copter/docs/terrain-following.html).
- Wide-FOV proximity at low AGL often “sees” the **ground** as an obstacle. Use `AVOID_ALT_MIN`, `OA_DB_ALT_MIN`, and (where available) `PRX_IGN_GND = 1`, plus a valid down rangefinder.
- Wire fences and thin branches are hard for every sensor; plan missions with **polygon fences / stay-outs** for known hazards and keep speed low.

Official sensor indexes:

- [Rangefinders landing page](https://ardupilot.org/copter/docs/common-rangefinder-landingpage.html)
- [Proximity sensors landing page](https://ardupilot.org/copter/docs/common-proximity-landingpage.html)
- [Rangefinder setup overview](https://ardupilot.org/copter/docs/common-rangefinder-setup.html)
- [LightWare SF45/B](https://ardupilot.org/copter/docs/common-lightware-sf45b.html)
- [RPLidar A2/C1/S1](https://ardupilot.org/copter/docs/common-rplidar-a2.html)
- [Benewake TFmini / Plus](https://ardupilot.org/copter/docs/common-benewake-tfmini-lidar.html)
- [Optical flow setup](https://ardupilot.org/copter/docs/common-optical-flow-sensor-setup.html)

---

## 2. Companion computer: Raspberry Pi vs Jetson (when needed)

| Approach | When | Notes |
|---|---|---|
| **FC-only** | Default for compact sprayer | Scanning LiDAR or ≤9 rangefinders → AP_Proximity → Simple Avoidance + BendyRuler. Lowest mass, power, and failure surface. |
| **Raspberry Pi** (3/4/5) | MAVLink bridging, BlueOS/Rpanion, light `OBSTACLE_DISTANCE` from a simple lidar driver, logging | Fine as companion **radio/GCS bridge**; weak for RealSense-class dense depth at useful Hz while spraying. |
| **Jetson** (Nano / Orin Nano class) | Depth camera OA, onboard weed ID / vision, ROS 2 | Use when you need stereo/RealSense → `DISTANCE_SENSOR` / `OBSTACLE_DISTANCE_3D` into `PRXx_TYPE = 2`. Heavier power/thermal budget on a compact airframe. |

**Rule of thumb:** if the sensor already has an ArduPilot serial/I2C/CAN driver, **do not add a companion for OA**. Add Pi/Jetson only for vision pipelines the FC cannot run. Developer overview of MAVLink proximity feed: [Copter Object Avoidance (dev)](https://ardupilot.org/dev/docs/code-overview-object-avoidance.html).

---

## 3. ArduPilot integration

Two layers matter for Guided waypoint spraying:

1. **Simple Object Avoidance** — reactive stop/slide in **Loiter / AltHold** (pilot or position-hold), using proximity + optional fences.
2. **Path planners (OA_TYPE)** — active path bending in **AUTO / GUIDED / RTL**.

### 3.1 Simple Avoidance + proximity

Docs: [Simple Object Avoidance](https://ardupilot.org/copter/docs/common-simple-object-avoidance.html)

Typical ideas (adapt to your wiring; treat as examples):

- `AVOID_ENABLE = 7` — use all barrier sources including proximity (confirm bitmask meaning in your firmware version).
- `PRX1_TYPE` — model-specific (e.g. `8` = LightWare SF45B, `5` = RPLidarA2, `4` = RangeFinder aggregate, `2` = MAVLink).
- Horizontal rangefinders: configure `RNGFNDx_*` then `PRX1_TYPE = 4`.
- `AVOID_MARGIN`, `AVOID_BEHAVE` (Stop vs Slide in Loiter), `AVOID_BACKUP_SPD`, `AVOID_ACCEL_MAX`, `AVOID_ALT_MIN`.
- Safety: map an RC option **40** (Proximity Avoidance) so you can disable OA in flight on first tests.

**Constraint:** only **one** proximity source class at a time — either one 360° lidar **or** up to nine rangefinders (not both as simultaneous PRX sources).

### 3.2 Path planning: `OA_TYPE`

| `OA_TYPE` | Algorithm | Uses proximity? | Uses fences? | Modes |
|---|---|---|---|---|
| `0` | Disabled | — | — | — |
| `1` | **BendyRuler** | Yes (OA database from PRX) | Yes | AUTO, GUIDED, RTL |
| `2` | **Dijkstra** | **No** (fences / stay-outs only) | Yes | AUTO, GUIDED, RTL |
| `3` | **Dijkstra + BendyRuler fusion** | Yes (BendyRuler when PRX hits) | Yes (Dijkstra between) | AUTO, GUIDED, RTL |

Official pages:

- [BendyRuler](https://ardupilot.org/copter/docs/common-oa-bendyruler.html)
- [Dijkstra](https://ardupilot.org/copter/docs/common-oa-dijkstras.html)
- [Dijkstra + BendyRuler](https://ardupilot.org/copter/docs/common-oa-dijkstrabendyruler.html)

**BendyRuler** (`OA_TYPE = 1`) probes many directions for free space and picks a path that clears obstacles while progressing to the destination. Important params:

- `OA_BR_LOOKAHEAD` — probe distance (m); obstacles farther are ignored (≈5 m typical starter).
- `OA_MARGIN_MAX` — stay-away distance (m) (≈2 m typical starter).
- `OA_BR_TYPE` — `1` horizontal (default for most spray paths); `2` vertical (climb/descend over short obstacles — use altitude fence; risky near polygon fences).
- Database: `OA_DB_SIZE`, `OA_DB_EXPIRE`, `OA_DB_QUEUE_SIZE`, `OA_DB_OUTPUT`, `OA_DB_ALT_MIN`.

**Dijkstra** (`OA_TYPE = 2`) builds a shortest path around **fences and stay-out zones only**. Official warning: **does not avoid lidar/proximity obstacles** and does not support spline waypoints. For Guided, set `GUID_OPTIONS` bit 6 (e.g. 64) as documented.

**Fusion** (`OA_TYPE = 3`) runs Dijkstra for fence routing and switches to BendyRuler when proximity obstacles appear, then returns to Dijkstra when clear. Best when you have **both** good fence maps of known tree clumps **and** a live PRX sensor for surprises.

Libraries (for developers): `AC_Avoidance` / `AP_OAPathPlanner`, `AP_OABendyRuler`, `AP_OADijkstra`, `AP_OADatabase`, plus `AP_Proximity`.

### 3.3 Modes cheat-sheet (Copter)

| Need | Mode / feature | Key enablers |
|---|---|---|
| Pilot Loiter stop/slide | Simple Avoidance | PRX + `AVOID_*` |
| Guided / Auto path around trees | BendyRuler or fusion | `OA_TYPE` 1 or 3 + PRX |
| Known keep-out paddocks / tree belts | Dijkstra or fusion | Fences + `OA_TYPE` 2 or 3 |
| Hold AGL over ridges | Surface Tracking / Terrain Following | Downward `RNGFND` |
| GPS-weak hover assist | Optical flow + down RFND | FLOW + EKF sources — **not OA** |

---

## 4. Minimal lightweight recommendation (won’t overload Pixhawk)

**Goal:** Guided spot-spray, compact airframe, hills — **FC-direct**, no companion required for OA.

### Preferred minimal kit

1. **Downward Benewake TFmini Plus** (UART preferred) — AGL / Surface Tracking / landing.  
   Configure as `RNGFNDx` with `ORIENT = 25` (down).  
   Docs: [TFmini](https://ardupilot.org/copter/docs/common-benewake-tfmini-lidar.html), [Surface Tracking](https://ardupilot.org/copter/docs/terrain-following-manual-modes.html).

2. **Horizontal proximity — pick one:**
   - **Best single-sensor:** LightWare **SF45/B** on a free UART (`SERIALx_PROTOCOL = 11`, `PRX1_TYPE = 8`). ~50 g, large horizontal FOV.  
     Docs: [SF45/B](https://ardupilot.org/copter/docs/common-lightware-sf45b.html).
   - **Lightest / cheapest:** **2–3× TFmini Plus** (forward + ±45° or left/right), `RNGFNDx_ORIENT` set, then `PRX1_TYPE = 4`. Accept narrower coverage.

3. **Software on FC**
   - Simple Avoidance for Loiter tests: `AVOID_ENABLE`, margins, RC disable (option 40).
   - Mission OA: start with **`OA_TYPE = 1` (BendyRuler)**, `OA_BR_TYPE = 1` (horizontal), conservative `OA_BR_LOOKAHEAD` / `OA_MARGIN_MAX`, low Guided speed.
   - Add polygon **fences** for known tree lines; if you rely on them heavily, consider **`OA_TYPE = 3`**.
   - Use `AVOID_ALT_MIN` / `OA_DB_ALT_MIN` / ground-ignore options so low AGL hill flight does not treat the slope as a wall.

4. **Defer**
   - Optical flow — only if GPS is poor under canopy; still needs the down rangefinder.
   - Ultrasonics — backup only.
   - mmWave — optional later for mist/dust if you pick a **wiki-listed** unit with native driver.
   - Pi/Jetson + RealSense — only when you need vision OA or onboard perception; Jetson if depth is required.

### Why this won’t overload the Pixhawk

- Proximity consolidation into coarse sectors and BendyRuler/Dijkstra are designed to run on the FC avoidance thread; Dijkstra deliberately **excludes** dense proximity maps because of CPU cost (hence fusion uses BendyRuler for live obstacles).
- Avoid feeding unfiltered 3D point clouds into the FC; if you ever use radar/depth cameras, **filter on the companion** and send sparse MAVLink distances (official guidance in discourse/dev docs for mmWave clutter).

### Example parameter sketch (illustrative only)

```text
# Down AGL (SERIAL4 example) — Benewake TFmini Plus
SERIAL4_PROTOCOL = 9
SERIAL4_BAUD     = 115
RNGFND1_TYPE     = 20
RNGFND1_MIN_CM   = 10
RNGFND1_MAX_CM   = 600
RNGFND1_ORIENT   = 25

# Horizontal PRX — SF45/B on SERIAL1 (alternative: PRX1_TYPE=4 + more RNGFNDs)
SERIAL1_PROTOCOL = 11
SERIAL1_BAUD     = 115
PRX1_TYPE        = 8
PRX1_ORIENT      = 0

# Simple Avoidance (Loiter) + path OA (Guided/Auto)
AVOID_ENABLE     = 7
AVOID_MARGIN     = 2
OA_TYPE          = 1
OA_BR_TYPE       = 1
OA_BR_LOOKAHEAD  = 5
OA_MARGIN_MAX    = 2

# RC switch to disable proximity avoidance while testing (example ch8)
RC8_OPTION       = 40
```

Re-check every name/value against **your** ArduCopter version’s parameter documentation before flight.

---

## Best official links (bookmark these)

| Topic | URL |
|---|---|
| Simple Object Avoidance | https://ardupilot.org/copter/docs/common-simple-object-avoidance.html |
| BendyRuler | https://ardupilot.org/copter/docs/common-oa-bendyruler.html |
| Dijkstra | https://ardupilot.org/copter/docs/common-oa-dijkstras.html |
| Dijkstra + BendyRuler | https://ardupilot.org/copter/docs/common-oa-dijkstrabendyruler.html |
| Proximity landing page | https://ardupilot.org/copter/docs/common-proximity-landingpage.html |
| Rangefinders landing page | https://ardupilot.org/copter/docs/common-rangefinder-landingpage.html |
| Rangefinder setup overview | https://ardupilot.org/copter/docs/common-rangefinder-setup.html |
| SF45/B | https://ardupilot.org/copter/docs/common-lightware-sf45b.html |
| RPLidar | https://ardupilot.org/copter/docs/common-rplidar-a2.html |
| Benewake TFmini | https://ardupilot.org/copter/docs/common-benewake-tfmini-lidar.html |
| Optical flow | https://ardupilot.org/copter/docs/common-optical-flow-sensor-setup.html |
| Surface Tracking | https://ardupilot.org/copter/docs/terrain-following-manual-modes.html |
| Terrain Following (Auto) | https://ardupilot.org/copter/docs/terrain-following.html |
| Dev: OA / DISTANCE_SENSOR | https://ardupilot.org/dev/docs/code-overview-object-avoidance.html |

---

## Safety reminders

- OA algorithms change between releases; first flights with an **RC disable** for avoidance and a clear abort plan.
- Low-altitude hill spraying: prioritize **reliable AGL** (down LiDAR + surface/terrain following) and **slow Guided speeds** before aggressive horizontal OA margins.
- Never treat OA as permission to fly closer to trees or power lines than visual observers and local rules allow.
