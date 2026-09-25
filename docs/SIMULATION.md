# Simulation & testing — SITL, HIL, and spray QA

How to exercise the precision touch-up sprayer **without chemical flight**:
ArduPilot **SITL**, **HIL** with a real Pixhawk, a **virtual sprayer** that
logs every `MAV_CMD_DO_SPRAYER`, and a staged test sequence. Complements
[AIRFRAME.md](AIRFRAME.md), [INTEGRATION.md](INTEGRATION.md),
[LIVE-PIPELINE.md](LIVE-PIPELINE.md), [STACK.md](STACK.md),
[VOICE-CONTROL.md](VOICE-CONTROL.md), [WAYPOINT-OPT.md](WAYPOINT-OPT.md), and
[SETUP-NOTES.md](SETUP-NOTES.md). Compiled 2026-09-26.

**Ops shape (same as live):** ~**25 weeds** / ~**5 acres** touch-up; live ID on
**GS**; **Pi 3B+** bridge/logs only; spray via **`MAV_CMD_DO_SPRAYER` (216)** or
aux **15**; gates **`conf ≥ 0.50`**, **georeg \(g \ge 0.70\)**; **ELRS** hard RC
authority; **WFB-NG** video pixels only; telem on **SiK / ELRS telem** (never
video RF).

This is a **project spec**, not an official ArduPilot procedure. Props off,
pump disconnected, and water-only until stage 4 criteria pass.

---

## 0. Goals and non-goals

| Goal | Non-goal |
|------|----------|
| Prove georeg + spray gates without air hardware | Replace outdoor calibration of pump / GSD |
| Log every spray pulse like a real pass | Certified chemical deposition model |
| Stage SITL → HIL → bench pump before flight | Full photoreal WFB-NG RF simulation |
| Match authority order ([INTEGRATION.md](INTEGRATION.md) §1) | Voice or live ID overriding ELRS |

**Locked FC class for sim:** Pixhawk 4 / Cube Orange–class Copter build **with
`AC_Sprayer`** ([AIRFRAME.md](AIRFRAME.md) §2, [STACK.md](STACK.md) §1). Do not
validate spray logic on 1 MB limited firmware that strips sprayer.

---

## 1. SITL — software-in-the-loop

### 1.1 What SITL stands in for

| Real (ops) | SITL stand-in |
|------------|---------------|
| F450 + Pixhawk 4 / Cube + ArduCopter + `AC_Sprayer` | `sim_vehicle.py -v ArduCopter` (quad default ≈ F450-class mass/Ixx; tune later) |
| ELRS TX/RX → FC `RCIN` | Joystick → MAVProxy / RC override, or `RC_CHANNELS_OVERRIDE` from a GS script |
| SiK / ELRS telem | UDP/TCP MAVLink (`udp:127.0.0.1:14550` typical) |
| Pi 3B+ UART bridge | Virtual serial: `socat` PTY pair **or** `mavlink-router` endpoint to a “companion” process |
| Thinker + WFB video | Fake frames **or** inject detections (no WFB RF stack required in SITL) |
| Live ID on GS | Same GS pipeline code paths; feed synthetic video / JSON detections |
| Pump PWM | Virtual sprayer logger (§3) — no GPIO |

### 1.2 Practical defaults (how to run)

Assume a normal ArduPilot checkout and tools on the GS host (or a Linux VM).

```bash
# Terminal A — Copter SITL with console + map (adjust path to your ardupilot tree)
cd ~/ardupilot
Tools/autotest/sim_vehicle.py -v ArduCopter -f quad --console --map \
  --out=udp:127.0.0.1:14550
```

In MAVProxy (or Mission Planner / QGC connected to the same UDP):

1. Load spray-related params from a **copy** of `config/example-spray-params.param`
   (edit SERVO channel numbers for the sim mapping).
2. `SPRAY_ENABLE = 1`, refresh; map a sim servo to function **22** (pump).
3. Optional: `SCR_ENABLE = 1` and load `scripts/example-sprayer-rtl-off.lua` to
   verify RTL shutoff in SITL before HIL ([SETUP-NOTES.md](SETUP-NOTES.md) §E/J).
4. Arm in Guided / AUTO only after geofence / home are set for the virtual paddock.

**RC:** prefer a USB joystick through MAVProxy `module load joystick`, or a
small pymavlink RC-override loop that mimics ELRS stick authority. Never treat
MAVLink mode/spray as “RC”.

**Companion (Pi stand-in):**

```bash
# Example: PTY pair — SITL SERIAL ↔ companion process
socat -d -d PTY,link=/tmp/sitl_fc,raw,echo=0 PTY,link=/tmp/sitl_pi,raw,echo=0
# Point SITL serial out at /tmp/sitl_fc; companion opens /tmp/sitl_pi at 921600
# Or: mavlink-router with UDP endpoint to GS + serial endpoint to companion app
```

Pi role stays **bridge + logs** ([AIRFRAME.md](AIRFRAME.md) §3) — no live YOLO
authority in SITL either.

### 1.3 Synthetic weed field (~25 weeds / ~5 acres)

**Paddock:** ~**5 acres ≈ 2.02 ha**. Use a rectangle ≈ **142 m × 142 m**
(~20 164 m²) centred on a local origin (or any rural lat/lon with a flat DEM
stub). Store weeds as **GeoJSON FeatureCollection** under
`data/sim/weed_field.geojson`.

| Parameter | Locked default |
|-----------|----------------|
| Weed count | **25** (±2 for RNG) |
| Distribution | Uniform random in paddock, then optional DBSCAN-like clustering (~15–25 m) so patches match [WAYPOINT-OPT.md](WAYPOINT-OPT.md) / live sparse ops |
| Properties per feature | `weed_id`, `class_id`, `canopy_cm2`, `planted: true` |
| RNG seed | Document in GeoJSON `properties.seed` for reproducibility |

**Generator (spec):** `scripts/sim_weed_field.py` — writes GeoJSON (+ optional
CSV). Example:

```bash
python3 scripts/sim_weed_field.py \
  --acres 5 --count 25 --seed 42 \
  --origin-lat -33.86 --origin-lon 151.21 \
  --out data/sim/weed_field.geojson
```

### 1.4 Feeding live pipeline without air hardware

Two injection modes (both valid; pick per test):

| Mode | Mechanism | Use when |
|------|-----------|----------|
| **A — Fake video** | Render simple 720p frames: green pasture + coloured blobs at projected weed pixels; RTP/UDP to the GS ingest port (`:5600` per [LIVE-PIPELINE.md](LIVE-PIPELINE.md)) | Exercising ROI → ONNX → georeg end-to-end |
| **B — Inject detections** | Bypass ONNX; push synthetic detections (`conf`, mask/box, pixel centroid) + telem into the georeg + spray-decision module | Unit-testing gates / dose / cooldown |

**Telem association:** SITL already emits `GLOBAL_POSITION_INT` / `ATTITUDE` /
GPS. Keep the live pipeline’s ≤ **80 ms** telem-age drop and **\(g \ge 0.70\)**
gate. For mode A, set camera intrinsics to a known sim \(K\) in
`config/camera-intrinsics.yaml` (or a `config/camera-intrinsics-sim.yaml`
override).

**Spray path:** GS decision → telem MAVLink **`MAV_CMD_DO_SPRAYER`** (or aux
**15**) → SITL `AC_Sprayer` → virtual logger (§3). Video RF is **not**
simulated beyond local RTP — matches “WFB carries pixels only”.

### 1.5 SITL verification checklist

| Check | Pass |
|-------|------|
| Georeg projects each injected/fake weed within **≤ 0.5 m** of GeoJSON truth (flat paddock, known \(K\)) | Mean lateral error ≤ 0.5 m; no spray if \(g < 0.70\) |
| `conf < 0.50` detections never fire pump | Zero `DO_SPRAYER` enables for those IDs |
| `conf ≥ 0.50` and \(g \ge 0.70\) under boom → one pulse; cooldown **3 s / 0.5 m cell** holds | Matches [LIVE-PIPELINE.md](LIVE-PIPELINE.md) §4.4 |
| RTL / Land / disarm forces sprayer off (Lua or GS) | Logger shows stop; no stuck-on |
| Companion bridge receives same MAVLink as GS UDP fan-out | No second spray writer without arbiter ([INTEGRATION.md](INTEGRATION.md) §4) |

---

## 2. HIL — hardware-in-the-loop

### 2.1 Practical defaults

| Choice | Default | Alternatives |
|--------|---------|--------------|
| Flight sim | **jMAVSim** (simple, free, ArduPilot-friendly) **or** ArduPilot **Gazebo / Physics** backends already used with `sim_vehicle` HIL modes | **RealFlight** (good Copter feel; Windows + RealFlight licence + ArduPilot RF link); **X-Plane** (heavier setup) |
| FC | **Real** Pixhawk 4 or Cube Orange (USB/serial to host) | — |
| Firmware | ArduCopter **with `AC_Sprayer`**, same param set as air | — |
| RC | **Real ELRS** TX + RX into FC `RCIN` / CRSF | USB joystick only if ELRS HW unavailable (note as degraded HIL) |
| Telem | **Real SiK** pair (or ELRS telem) FC ↔ GS | USB MAVLink alone for bench bring-up |
| Companion | **Real Pi 3B+** UART/USB MAVLink bridge + logs | Host process only for early HIL |

**Locked pick for this project’s HIL day-1:** real **Pixhawk 4 / Cube** +
**jMAVSim** (or Gazebo if already installed) + **real ELRS** + **real SiK** +
**real Pi 3B+**. Document if RealFlight is used instead for handling feel.

### 2.2 What is real vs simulated

| Item | HIL |
|------|-----|
| Flight controller | **Real** |
| Sensors (IMU, baro, …) as used by EKF | **Real on FC**; airframe motion **simulated** by the flight sim feeding HIL sensor/physics as configured |
| Motors / props / airframe | **Simulated** (props **physically off**) |
| ELRS RC | **Real** RF path TX→RX→FC |
| SiK / ELRS telem | **Real** radios (or USB for partial HIL) |
| Pi 3B+ | **Real** companion |
| Camera / WFB | **Simulated** (fake frames / inject) unless a ground camera is fed in |
| Pump / boom / chemical | **Disconnected** until stage 4; virtual logger still runs |
| Live ID GS software | **Real** code on GS |

### 2.3 Safety (mandatory)

- **Props removed** or otherwise unable to spin under power.
- Power FC / ESCs from a **bench PSU or bench battery** with current limits;
  treat arming as hazardous even without props.
- **Pump electrical disconnected** from fluid until stage 4; use a LED /
  logic analyser / servo-rail monitor on the sprayer PWM channel in stages
  1–3.
- Geofence / soft limits in the flight sim; no outdoor RF tests that violate
  local rules.
- Keep [INTEGRATION.md](INTEGRATION.md) authority: ELRS can always inhibit
  spray; GS must not fight RC sprayer-off.

### 2.4 HIL verification checklist

| Check | Pass |
|-------|------|
| Stick inputs on ELRS move sim vehicle / modes on FC | Modes match TX |
| Telem SiK (or ELRS telem) delivers `COMMAND_ACK` for `DO_SPRAYER` | ACK OK; latency logged |
| Pi bridge forwards spray cmds and writes onboard log copy | Matches GS JSONL event IDs |
| Fake/injected live ID pulses appear on real servo pump channel PWM | Scope / GCS Servo output |
| Failsafe / RC loss → pump PWM off | Per Lua + FC failsafes |

---

## 3. Simulated spraying — virtual sprayer & coverage review

### 3.1 Virtual sprayer model

In SITL and HIL (stages 1–3), a **virtual sprayer** process on the GS (and
optionally the Pi) subscribes to MAVLink and logs every sprayer actuation:

| Field | Source |
|-------|--------|
| `t_utc` / monotonic | Logger clock |
| `cmd` | `MAV_CMD_DO_SPRAYER` (216) param1 1/0, or aux **15** high/low |
| `lat`, `lon`, `alt_m` | Nearest `GLOBAL_POSITION_INT` |
| `gs_m_s`, `yaw_deg` | Telem |
| `dose_mL` | From live-pipeline decision log if present; else `null` |
| `weed_id` | From decision log / GeoJSON nearest neighbour within 1.0 m |
| `section` | Pipeline section index if known |
| `pwm_pump` | Optional: SERVOn raw if available in SITL |

**Logger (spec):** `scripts/spray_sim_logger.py` →
`logs/sim/spray_events.jsonl` (one JSON object per enable/disable or per
completed pulse).

Post-flight review matches a real pass: sort by time, join to
`data/sim/weed_field.geojson`, classify **hit / miss / false spray**.

### 3.2 Coverage visualisation

| View | Requirement | Tool (spec) |
|------|-------------|-------------|
| **2D (required)** | Plot paddock outline, weed points, spray hit markers (and miss circles) | `scripts/plot_spray_coverage.py` → PNG under `logs/sim/` |
| **3D (optional)** | Same with AGL / DEM if present | matplotlib 3D or simple Cesium/kepler.gl export later |

**Hit rule (v1):** spray pulse position within **1.0 m** horizontal of a weed
feature → hit that `weed_id` (nearest). Unmatched pulse → **false spray**.
Unmatched weed after mission → **miss**.

### 3.3 Proposed script paths

| Path | Role |
|------|------|
| `scripts/sim_weed_field.py` | Random ~25-weed / ~5-acre GeoJSON generator |
| `scripts/spray_sim_logger.py` | MAVLink listener → JSONL spray event log |
| `scripts/plot_spray_coverage.py` | 2D (required) coverage plot; optional 3D flag |

Stubs may live in-repo as runnable sketches; behaviour above is **normative**.

---

## 4. Test sequence (mandatory order)

Do **not** skip ahead. Each stage needs a recorded pass before the next.

### Stage (a) — SITL weed-field run

1. Generate `data/sim/weed_field.geojson` (seed documented).
2. Start Copter SITL + spray params + virtual logger.
3. Run live pipeline in **inject** or **fake-video** mode over the virtual
   paddock (Guided/AUTO transect or clustered waypoints from
   [WAYPOINT-OPT.md](WAYPOINT-OPT.md)).
4. Produce coverage PNG + JSONL.

| Criterion | Pass | Fail |
|-----------|------|------|
| Spray gate fidelity | No fires below conf 0.50 or \(g < 0.70\) | Any ungated enable |
| Georeg | Mean error ≤ 0.5 m vs truth (flat sim) | Systematic > 0.5 m |
| Coverage | ≥ **90%** of planted weeds hit in opportunistic or mission mode | < 90% without documented inject fault |
| False sprays | ≤ **2** unmatched pulses per 25-weed run | Spray on empty pasture beyond that |
| RTL shutoff | Pump stops on RTL/Land | Stuck-on in log |

### Stage (b) — HIL with real radios

1. Props off; pump disconnected; bench power.
2. Real FC + jMAVSim (or chosen alt) + **real ELRS** + **real SiK** + **real
   Pi 3B+**.
3. Repeat synthetic field / inject path; confirm PWM on sprayer channel and
   dual logs (GS + Pi).

| Criterion | Pass | Fail |
|-----------|------|------|
| RC authority | ELRS mode/arm/sprayer inhibit works | Need USB joystick to recover |
| Telem spray path | `DO_SPRAYER` ACK over SiK/ELRS telem | ACK only on USB when radios are the SUT |
| Pi bridge | Events mirrored; no competing spray writers | Duplicate enables |
| Same gate metrics as (a) | Within same pass bands on logger | Gate regressions vs SITL |

### Stage (c) — Bench power-up of real sprayer pump

1. Still **props off**; aircraft secured.
2. Connect pump to **water** (or catch bottle); no chemical.
3. Drive pump from **simulated / logged commands** (SITL or HIL GS pulses),
   not from outdoor live ID yet.
4. Measure pulse volume vs commanded \(V_{\mathrm{mL}}\)
   ([LIVE-PIPELINE.md](LIVE-PIPELINE.md) §4.2); adjust PWM / \(Q\) calibration
   notes ([SETUP-NOTES.md](SETUP-NOTES.md) §C).

| Criterion | Pass | Fail |
|-----------|------|------|
| Command → flow | Each logged enable produces measurable flow within **±20%** of target \(V\) after cal | No flow / stuck-on |
| Stop latency | Disable → flow stops ≤ **0.5 s** | Continues > 0.5 s |
| Inhibit | RC sprayer-off and RTL path stop pump | GS can force on against RC off |
| Leak / wiring | No shorts, no dry-run damage beyond vendor limits | Overheat / fuse trip unexplained |

**After (c) pass:** outdoor water flights per [SETUP-NOTES.md](SETUP-NOTES.md)
§H, then chemical only under local law / SOP. Live ID remains shadow-mode until
[LIVE-PIPELINE.md](LIVE-PIPELINE.md) §7 offline acceptance bar is met.

---

## 5. Logging layout (repo)

```
data/sim/weed_field.geojson      # synthetic truth
logs/sim/spray_events.jsonl      # virtual sprayer
logs/sim/coverage_2d.png         # required plot
logs/live-pipeline.jsonl         # GS decisions (existing convention)
```

Retain seed, firmware hash, and param file name in a short
`logs/sim/RUN.md` per formal run.

---

## 6. One-paragraph summary

Simulation for this touch-up sprayer is a three-stage gate: **SITL**
(ArduCopter + `AC_Sprayer`, ELRS-equivalent RC override, UDP telem, Pi via
virtual serial) flies a **~25-weed / ~5-acre GeoJSON** field into the GS live
pipeline (fake frames or injected detections) to prove **conf ≥ 0.50**,
**georeg ≥ 0.70**, and spray cooldowns; **HIL** then attaches a **real Pixhawk
4 / Cube**, **real ELRS**, **real SiK**, and **real Pi 3B+** to jMAVSim (or
RealFlight/X-Plane) with props off and pump disconnected; a **virtual sprayer
logger** records every `DO_SPRAYER` / aux-15 with pose and dose for **2D
coverage plots**; only after SITL and HIL pass does **bench water** exercise
the real pump from simulated commands before any outdoor or chemical work.

---

## Related docs

- [AIRFRAME.md](AIRFRAME.md) — F450, Pixhawk 4 / Cube, Pi 3B+ roles
- [INTEGRATION.md](INTEGRATION.md) — authority order, buses (ELRS / WFB / telem)
- [LIVE-PIPELINE.md](LIVE-PIPELINE.md) — conf / georeg gates, dose, `DO_SPRAYER`
- [STACK.md](STACK.md) — `AC_Sprayer`, MAVLink 216 / aux 15
- [VOICE-CONTROL.md](VOICE-CONTROL.md) — SITL-first convenience layer (not RC)
- [WAYPOINT-OPT.md](WAYPOINT-OPT.md) — clustered ~25-weed missions
- [SETUP-NOTES.md](SETUP-NOTES.md) — bench checklist, Lua, first-flight order
