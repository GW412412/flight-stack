# Gap analysis — end-to-end precision spot-spray system

Compiled 2026-09-26. Derived only from existing project docs (no invented SKUs,
prices, or locks). Recommendations are labelled as such and are **not** locks.

---

## 1. Purpose / how to read

| Tag | Meaning |
|-----|---------|
| **Specified** | Named and locked (or clearly chosen) in a project doc — enough to buy, wire, or implement without a further product decision |
| **Gap** | Missing, vague, example-only, or “mentioned without params/procedure” — blocks a safe complete field system until filled |
| **Optional** | Explicitly out of scope for v1, or marked optional (e.g. OpenRTLS, K80 EP, per-section valves) |

**How to use:** §2 inventories what *is* specified. §3 lists gaps with
**P0 / P1 / P2**, why each blocks, and a suggested next doc or decision. Top
three P0 items are repeated in §1.1 for quick triage.

This file does **not** replace module specs. When a gap is filled, lock it in
the owning doc (AIRFRAME, INTEGRATION, SETUP-NOTES, etc.) and shrink this list.

### 1.1 Top three highest-priority missing items (P0)

1. **Power distribution and dedicated BEC rails** — no PDB / rail map for FC vs Pi vs video TX vs pump; brownout risk blocks safe AUW integration.
2. **Locked failsafe behaviours (FS_* params + production Lua)** — fail-closed rules exist as intent; concrete RC/telem/RTK/video/battery/tank/geofence responses are not parameter-locked.
3. **Sprayer hardware lock (pump, nozzle, boom geometry) + flow/dose bench procedure** — LIVE-PIPELINE dose → `t_on` needs calibrated \(Q\) and section geometry that are not yet chosen or documented as a field procedure.

---

## 2. Inventory — what IS specified

Cite only what the docs already lock or clearly choose. Grouped for build
planning.

### 2.1 Hardware components

| Component | Locked / chosen | Where |
|-----------|-----------------|-------|
| Frame | F450-class / Flamewheel 450 (~450 mm MTM), lightweight X | AIRFRAME |
| Props | 8-inch, 3-blade matched CW/CCW | AIRFRAME |
| Motors | 4× 2216-class ~880–920 KV on **4S** | AIRFRAME |
| ESCs | 4× 30–40 A BLHeli_S / AM32 (4-in-1 or discrete) | AIRFRAME |
| Battery | 4S LiPo **5000–6000 mAh** (≥25C); start 5000 for AUW | AIRFRAME |
| Flight controller | **Holybro Pixhawk 4** or **Cube Orange** (2 MB+ flash); **not** Pixhawk1/FMUv2 | AIRFRAME, INTEGRATION, STACK |
| Firmware | ArduCopter current stable **with `AC_Sprayer`**; Lua-capable | AIRFRAME, STACK, SETUP-NOTES |
| Companion | **Raspberry Pi 3B+** only (bridge/logs; **not** live YOLO authority) | AIRFRAME, INTEGRATION |
| Camera / air unit | **OpenIPC Thinker (Base) + IMX335** | SYSTEM-SPEC, OPENIPC-CAMERAS |
| Video Wi‑Fi (air + GS) | **RTL8812AU**, WFB-NG, **5 GHz**, 20 MHz | SYSTEM-SPEC |
| RC | **ELRS 2.4 GHz** → FC CRSF/SBUS (hard authority) | AIRFRAME, INTEGRATION, SYSTEM-SPEC |
| RTK air | **Holybro H-RTK F9P Rover** (~USD 205) | AIRFRAME |
| RTK ground | **Holybro H-RTK F9P Base** (~USD 259), local paddock-edge base (not third-party NTRIP) | AIRFRAME |
| OA — down | **Benewake TFmini Plus** (AGL / surface tracking) | OBSTACLE-AVOIDANCE, AIRFRAME |
| OA — horizontal | **LightWare SF45/B** *or* 2–3× TFmini forward/side; `OA_TYPE = 1` BendyRuler (fusion 3 if fences) | OBSTACLE-AVOIDANCE |
| Spray tank | **0.5 L** soft/rigid, CG on centreline, concentrate mix | AIRFRAME, LIVE-PIPELINE |
| Pump role | Small **PWM** diaphragm/brushless on `SERVOn_FUNCTION = 22`; optional spinner `= 23` | AIRFRAME, STACK |
| Boom concept | Light **3-section** boom (L/C/R) or single nozzle under CG | AIRFRAME, LIVE-PIPELINE §4.3 |
| OpenRTLS (optional) | ≥4× DWM1000-class anchors + air tag; ~$100–200 minimal kit; UWB → Pi → `VISION_POSITION_ESTIMATE` | AIRFRAME §6.1, INTEGRATION §4.1 |
| GS compute (assumed) | 40-core CPU / 256 GB RAM; optional Tesla K80 for ONNX | LIVE-PIPELINE, WEED-ID, LABELING-WORKFLOW |

**Not invented here:** exact ELRS TX/RX boards, SiK radio SKU, pump model,
nozzle SKU, boom CAD, PDB part numbers — those are gaps (§3).

### 2.2 Radio / data links

| Link | Medium / role | Where |
|------|---------------|-------|
| RC | ELRS **2.4 GHz** — sticks, mode, arm, sprayer inhibit | INTEGRATION, AIRFRAME |
| Video | **WFB-NG 5 GHz** — H.264 pixels only; never RC/MAVLink/RTCM | SYSTEM-SPEC, INTEGRATION |
| Telemetry | SiK / ELRS telem / USB / Ethernet via Pi router — MAVLink modes, spray, mission, GPS | INTEGRATION |
| RTK corrections | F9P Base → GCS RTK Inject → telem → rover; **not** on video Wi‑Fi | AIRFRAME, STACK, INTEGRATION |
| OpenRTLS (opt.) | UWB + Ethernet/UART — local pose aid near canopy | AIRFRAME, INTEGRATION |
| GS IPC | localhost RTP `:5600` / RTSP restream + Unix sockets / JSONL logs | SYSTEM-SPEC, INTEGRATION |

Band coexistence rule is locked: **video 5 GHz, ELRS 2.4**; do not put both on
2.4 on a compact airframe (SYSTEM-SPEC, OPENIPC).

### 2.3 Software modules

| Module | Spec | Where |
|--------|------|-------|
| FC sprayer | `SPRAY_ENABLE`, `SERVOn_FUNCTION` 22/23, `RCx_OPTION = 15`, `MAV_CMD_DO_SPRAYER` (216) / aux 15 | STACK, SETUP-NOTES, AIRFRAME |
| Lua helpers (intent) | RTL / Land / Disarm pump off; tank-empty shutoff | STACK, SETUP-NOTES; sketch `scripts/example-sprayer-rtl-off.lua` |
| GS hub | Sole `COMMAND_LONG` writer; spray arbiter | INTEGRATION §6 |
| Video ingest | WFB decode → RTP/RTSP | SYSTEM-SPEC, LIVE-PIPELINE |
| Live ID | ROI → 640 → YOLOv8n-seg ONNX; conf ≥ 0.50; georeg \(g\) ≥ 0.70; dose clamp; section map | LIVE-PIPELINE |
| Offline weed map | ODM tiles → YOLO-seg → GeoJSON/CSV | WEED-ID, LABELING-WORKFLOW |
| Labeling | CVAT self-hosted; DeepWeeds-aligned classes; active-learning loop | LABELING-WORKFLOW |
| Waypoint opt | ATSP / OR-Tools; wind + DEM; 45° departure; cluster-aware revisit | WAYPOINT-OPT, LIVE-PIPELINE §4.5 |
| Voice | PTT/wake STT whitelist → read-back → `MAV_CMD_DO_SET_MODE`; ELRS hard authority | VOICE-CONTROL |
| OA on FC | Simple Avoidance + BendyRuler; example param sketch | OBSTACLE-AVOIDANCE |
| Companion stack options | mavlink-router / Rpanion / BlueOS (research); Pi role locked to bridge/logs | STACK, AIRFRAME |
| GCS options | Mission Planner / QGroundControl (research + mission patterns) | STACK, SETUP-NOTES |
| Example configs | `config/example-spray-params.param`, `example-companion-serial.param` | SETUP-NOTES, config/ |

### 2.4 Operational procedures already documented

| Procedure / rule | Status | Where |
|------------------|--------|-------|
| Authority order (RC > FC > GS > UI) | Locked | INTEGRATION §1 |
| Operating modes **A–E** (scout, live touch-up, clustered revisit, degraded, abort) | Locked | INTEGRATION §5 |
| Cross-cutting fail-closed rules (RC inhibit, video loss, georeg, OA, UWB aid-only) | Locked as **rules**; not fully param/Lua-implemented | INTEGRATION §7, LIVE-PIPELINE §6, SYSTEM-SPEC |
| Video link degradation steps 1–5 | Locked | SYSTEM-SPEC |
| Live latency budget ≤ 150 ms | Locked | LIVE-PIPELINE, SYSTEM-SPEC |
| Offline eval gates before live auto-spray (mAP50, recall, FP rate, zero-escape spot-check) | Locked | LIVE-PIPELINE §7 |
| Acceptance checks (video, airframe, OA, RTK, OpenRTLS when fitted) | Scattered checklists exist | SYSTEM-SPEC, AIRFRAME §9, OBSTACLE-AVOIDANCE |
| First-flight progression (no liquid → water → chemical) | Starter checklist | SETUP-NOTES §H |
| Spray rate calibration approach | Starter method for `SPRAY_PUMP_*` | SETUP-NOTES §C |
| Voice SITL test matrix before live | Locked | VOICE-CONTROL |
| Implementation order (SITL → voice → video → label → live shadow → waypoints → OA → field) | Locked | INTEGRATION §9 |
| Post-flight log **file names** | Named | INTEGRATION §8 |
| Concentrate mixing caveats (label rates, fresh per flight, discard leftover) | Partial (caveats, not full SOP) | LIVE-PIPELINE, AIRFRAME |
| Safety / regulatory non-exhaustive note | Pointer only | SETUP-NOTES §I |

---

## 3. Gaps — missing or only vaguely mentioned

Priority: **P0** blocks safe chemical / auto-spray field use; **P1** blocks
repeatable ops or procurement; **P2** polish / future.

### 3.1 Power distribution and BEC

| Field | Content |
|-------|---------|
| **Status** | **Gap (P0).** AIRFRAME §7 gives an approximate power budget and says power the Pi from a **dedicated 5 V / ≥3 A** BEC or known-good FC rail. Thinker has an integrated 3 A BEC (SYSTEM-SPEC). There is **no** PDB diagram, dedicated rail map, or redundancy plan for **pump vs FC vs Pi vs video TX vs OA**. |
| **Why it blocks** | Shared or undersized 5 V rails cause Pi SD corruption, MAVLink drops, and unsafe pump behaviour under hover current — cannot integrate the locked AUW stack safely. |
| **Suggested next** | New **`docs/POWER.md`** (or AIRFRAME §Power lock): rail voltages, BEC SKUs (**recommendation** until weighed), fuse/breaker map, pump on pack vs BEC decision, brownout test procedure. |

### 3.2 Failsafe behaviours

| Scenario | Documented today | Still missing |
|----------|------------------|---------------|
| RC / ELRS loss | Voice + live ID refuse; pump off on RC failsafe (INTEGRATION, LIVE-PIPELINE, AIRFRAME acceptance) | Concrete `FS_THR_*` / `FS_OPTIONS` / Copter radio-failsafe params; production Lua beyond example sketch |
| Telem loss | Live ID drops frames if telem age > 80 ms | FC-side policy when GS hub is gone mid-pulse (stop pump locally); no locked telem-failsafe params |
| RTK loss | Georeg \(g\) penalties; spray gate fails closed if \(g < 0.70\) | No FC mission behaviour lock (Loiter / slow / RTL) when Fix → Float mid-sortie |
| Video loss | Pump off; continue ELRS; precomputed waypoints only (SYSTEM-SPEC, LIVE-PIPELINE) | Detection method on hub (heartbeat from video_ingest) not specified as a param sheet |
| Low battery | Land with ≥ 20–25% remaining (AIRFRAME) | No `BATT_FS_*` / RTL voltage/capacity locks |
| Tank empty | Intent: Lua + flow/level/current proxy (STACK, SETUP-NOTES) | **No sensor chosen**; no production Lua; no bench threshold |
| Geofence | Concept + OA fences for trees (STACK, OBSTACLE-AVOIDANCE, SETUP-NOTES) | No fence SOP, inclusion/exclusion templates, or `FENCE_*` param sheet for no-spray zones |

| Field | Content |
|-------|---------|
| **Priority** | **P0** |
| **Why it blocks** | Chemical spot-spray without locked pump-off on RC/RTL/video/tank/battery is an unacceptable safety hole; rules-as-prose are not flight config. |
| **Suggested next** | **`docs/FAILSAFE.md`** + promote `scripts/example-sprayer-rtl-off.lua` to a tested script; param table per scenario; SITL acceptance matrix. |

### 3.3 Pre-flight checklist (formal field card)

| Field | Content |
|-------|---------|
| **Status** | **Gap (P1).** SETUP-NOTES and per-doc acceptance lists cover hangar/setup and some flight gates; there is **no** single printable **field card** (weather, RTK Fixed, ELRS RSSI, video latency, RC sprayer inhibit, tank/mix, PPE, geofence, OA disable switch, abort call). |
| **Why it blocks** | Scattered checks are easy to skip under paddock time pressure; auto-spray authorisation needs one signed gate. |
| **Suggested next** | **`docs/PREFLIGHT.md`** (one page + extended); cross-link SYSTEM-SPEC / AIRFRAME / LIVE-PIPELINE gates. |

### 3.4 Field calibration procedures

| Topic | Documented | Gap |
|-------|------------|-----|
| Compass / accel | “Standard Copter setup” (SETUP-NOTES) | No project-specific field card or MagFit notes for F450 + RTK mast |
| RTK survey-in | Base at paddock edge; Fixed before precision (AIRFRAME, SETUP-NOTES) | No timed survey-in SOP, base relocation checklist, or logged base coordinates template |
| Camera intrinsics | `config/camera-intrinsics.yaml` required (LIVE-PIPELINE) | File and calibration procedure **absent** |
| Flow / dose | Rate calibration sketch (SETUP-NOTES §C); LIVE-PIPELINE needs \(Q\) mL/s | No locked pump PWM→flow table; no concentrate viscosity note sheet |
| Thrust stand | Mandated before chemical (AIRFRAME §5, §9) | No stand procedure, pass criteria worksheet, or logging format |

| Field | Content |
|-------|---------|
| **Priority** | **P0** for flow/dose + thrust (tied to sprayer hardware); **P1** for compass/RTK/camera cards |
| **Why it blocks** | Dose pulses and 8×3 thrust margin are numerically claimed but not reproducible without calibration SOPs. |
| **Suggested next** | **`docs/CALIBRATION.md`** covering thrust, spray flow, camera \(K\), RTK survey-in; generate `config/camera-intrinsics.yaml` template. |

### 3.5 Data logging and post-flight review

| Field | Content |
|-------|---------|
| **Status** | **Gap (P1).** INTEGRATION §8 **names** JSONL / GeoJSON / waypoints and says bundle with FC `.bin` for auto-pulse sorties. No review workflow (who opens what, QA checklist, spray volume reconciliation, model shadow vs live compare, retention). |
| **Why it blocks** | Cannot close the loop on false sprays, escapes, or regulatory/agronomy review. |
| **Suggested next** | **`docs/POSTFLIGHT.md`**: bundle script outline, review checklist, sign-off fields. |

### 3.6 Spare parts list

| Field | Content |
|-------|---------|
| **Status** | **Gap (P2).** Pi “spare on hand” is mentioned (AIRFRAME); no BOM spares (props, arms, ESCs, antennas, Thinker, F9P, pumps, nozzles, BECs, SD cards). |
| **Why it blocks** | Field downtime; not a first-flight blocker if primary BOM is complete. |
| **Suggested next** | Spares column on a future **`docs/BOM.md`**. |

### 3.7 Safety interlocks (hardware + software)

| Field | Content |
|-------|---------|
| **Status** | **Gap (P0/P1).** Software fail-closed rules and RC sprayer inhibit are specified (INTEGRATION, LIVE-PIPELINE). **Hardware** interlocks are not: e.g. pump power cut on disarm/kill, physical arming plug, tank shutoff valve, boom tip guards, labelled kill path. “No module may open a second path to motor kill — hardware/RC only” is stated but the hardware path is not drawn. |
| **Why it blocks** | Software-only inhibit is insufficient for chemical systems if the hub or FC script fails. |
| **Suggested next** | Fold into **FAILSAFE.md** + AIRFRAME wiring: hardware pump enable tied to armed + RC aux; document kill switch wiring. |

### 3.8 Other real gaps found in the docs

| Gap | Priority | Why it blocks | Suggested next / decision |
|-----|----------|---------------|---------------------------|
| **ELRS TX/RX SKUs** unlocked (only “ELRS 2.4 GHz”) | P1 | Cannot procure matched air/ground RC without a product decision | Lock in AIRFRAME (mark **recommendation** until bought): TX module + RX form factor |
| **SiK / telem radio band + SKU for AU** | P1 | Telem path is required for spray MAVLink; AU ISM band/power rules not chosen | Decision: ELRS telem vs SiK 433/915 — lock band-legal AU option in AIRFRAME / new RADIOS note |
| **GS laptop / field GPU lock** | P1 | Live ID assumes 40-core/K80 **lab** GS; portable field GS not specified | Decide field GS (same box vs rugged laptop + eGPU); document in SYSTEM-SPEC or SETUP-NOTES |
| **Pump model** | P0 | “Small PWM diaphragm/brushless” is not buyable; PWM range unknown | Lock pump + driver in AIRFRAME / SPRAYER doc (**recommendation** until bench-tested) |
| **Nozzle type / orifice / spray quality** | P0 | Dose and drift depend on nozzle; SETUP-NOTES says “document nozzle type” but none locked | Lock nozzle + pressure target; deposition card test in CALIBRATION |
| **Boom geometry CAD / widths \(W\)** | P0 | LIVE-PIPELINE section index needs \(W\) and \(S=3\); no mm dimensions or mount drawing | CAD or dimensioned sketch in AIRFRAME; update LIVE-PIPELINE defaults with measured \(W\) |
| **Tank-empty / flow sensor hardware** | P0 | Lua tank-empty shutoff has no sensor | Choose level switch / flow meter / current sense; wire + Lua threshold |
| **Mixing protocol detail** | P1 | Caveats exist; no product-specific mix sheet, measuring gear, or disposal steps | **`docs/CHEMICAL-SOP.md`** (label-driven; no invented rates) |
| **Chemical handling / PPE** | P1 | SETUP-NOTES §I is non-exhaustive | Same CHEMICAL-SOP: PPE, spill, washout, storage |
| **Legal / radio licensing (AU)** | P1 | AIRFRAME assumes remote AU paddocks; SYSTEM-SPEC says tune legally; no CASA RPA / ACMA radio / ag chemical applicator checklist | **`docs/COMPLIANCE-AU.md`** (pointers + operator checklist; not legal advice) |
| **Insurance** | P2 | Not mentioned | Operator decision; note in COMPLIANCE |
| **GS service implementation** | P1 | INTEGRATION sketches `mavlink_hub.py` etc.; repo has extract_frames + example Lua only | Implement under `gs/` per INTEGRATION §9 order |
| **Waypoint-opt production defaults** | P2 | Research notes + LIVE-PIPELINE cluster radii; no single locked `mission_builder` config | Lock cluster radius / wind defaults in WAYPOINT-OPT when coding |
| **OpenRTLS “and also” follow-up slot** | Optional | Explicitly left open (AIRFRAME, INTEGRATION) | Fill when next radios/precision item is chosen; keep optional |
| **ODM flight / GSD SOP** | P1 | WEED-ID / LABELING assume ODM tiles; scout mission altitude/overlap not locked | Scout mission template in SETUP-NOTES or WEED-ID |
| **`camera-intrinsics.yaml` missing** | P1 | Referenced by LIVE-PIPELINE | Add template under `config/` after calibration |
| **Wiring harness beyond sketch** | P1 | AIRFRAME §8 ASCII sketch only | Harness drawing / pin table in AIRFRAME or POWER.md |
| **Visual observer / two-person ops** | P2 | SETUP-NOTES mentions observers “as required” | Field ops card in PREFLIGHT |
| **README / index gap** | P2 | README table omits GAPS (this file) and some module one-liners | Update README when committing |

---

## 4. Top three P0 items (expanded)

### P0-1 — Power distribution and dedicated BEC rails

AIRFRAME budgets currents but does not lock a power tree. The stack places
**FC + Pi (≥3 A @ 5 V) + Thinker/8812 TX + pulsed pump (2–8 A class) + OA lidars**
on one airframe. Until rails, fuses, and brownout tests are written, integration
acceptance (AIRFRAME §9) cannot be signed honestly.

**Fill with:** `docs/POWER.md` + update AIRFRAME wiring.

### P0-2 — Locked failsafe behaviours (params + production Lua)

Fail-closed **rules** are strong (INTEGRATION §7, LIVE-PIPELINE §6,
SYSTEM-SPEC). What is missing is the **flight configuration**: Copter
`FS_*` / battery / fence params, a non-example Lua script for RTL/Land/Disarm
(and tank empty once sensed), and a SITL pass record. Without that, Modes B/C
chemical flight is blocked by the project’s own safety intent.

**Fill with:** `docs/FAILSAFE.md` + tested script under `scripts/` + param snippet
under `config/`.

### P0-3 — Sprayer hardware lock + flow/dose bench procedure

LIVE-PIPELINE locks dose maths and \(t_{\mathrm{on}} = V/Q\), and AIRFRAME locks
0.5 L + 3-section concept, but **pump, nozzle, boom width, and \(Q\)(PWM)** are
unspecified. Thrust-stand *mandate* without a procedure sits in the same bucket
for first chemical hover.

**Fill with:** sprayer subsection lock in AIRFRAME (or `docs/SPRAYER.md`) +
`docs/CALIBRATION.md` flow and thrust sections.

---

## 5. Related docs

| Doc | Role |
|-----|------|
| [STACK.md](STACK.md) | Firmware, AC_Sprayer, companions, GCS, RTK patterns |
| [SYSTEM-SPEC.md](SYSTEM-SPEC.md) | Camera, WFB-NG, latency, video fallbacks |
| [INTEGRATION.md](INTEGRATION.md) | Authority, buses, modes A–E, fail-closed rules, log names |
| [AIRFRAME.md](AIRFRAME.md) | F450 / Pixhawk / Pi / RTK / AUW / power budget sketch |
| [LIVE-PIPELINE.md](LIVE-PIPELINE.md) | Live ID → georeg → dose → MAVLink |
| [VOICE-CONTROL.md](VOICE-CONTROL.md) | Voice whitelist / SITL gates |
| [LABELING-WORKFLOW.md](LABELING-WORKFLOW.md) | CVAT + YOLOv8n-seg training loop |
| [WEED-ID.md](WEED-ID.md) | Offline ODM tiling / inference |
| [WAYPOINT-OPT.md](WAYPOINT-OPT.md) | ATSP / wind / 45° departure |
| [OBSTACLE-AVOIDANCE.md](OBSTACLE-AVOIDANCE.md) | FC-only OA kit |
| [OPENIPC.md](OPENIPC.md) | OpenIPC RF / ingest research |
| [OPENIPC-CAMERAS.md](OPENIPC-CAMERAS.md) | Thinker / Mario / WiFiLink hardware notes |
| [SETUP-NOTES.md](SETUP-NOTES.md) | Starter setup ordering and safety pointers |
| [README.md](../README.md) | Repo map |

---

## 6. Quick counts (for triage)

| Bucket | Approx. count |
|--------|----------------|
| **Specified** hardware / link / software / ops items in §2 | **~55** line items across the four inventory tables |
| **Gaps** called out in §3 (including sub-rows in other-gaps table) | **~28** distinct gaps |
| Of which **P0** | **7** theme areas (power; failsafe; pump/nozzle/boom+flow; tank sensor; hardware interlocks — with top three highlighted in §1.1) |
| **Optional** | OpenRTLS; K80 CUDA EP; per-section valves beyond logging; wake-word vs PTT |

These counts are inventory lines, not story points. Closing the three P0 items
in §1.1 unblocks chemical touch-up trials; P1 items unblock repeatable paddock
ops and procurement.
