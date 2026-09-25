# Airframe & companion — F450 + Pixhawk + Pi 3B+

Minimal locked airframe for the precision touch-up sprayer. Complements
[INTEGRATION.md](INTEGRATION.md), [STACK.md](STACK.md), and
[LIVE-PIPELINE.md](LIVE-PIPELINE.md). Compiled 2026-09-26.

**Simplicity rule:** one **32-bit Pixhawk-class FC**, one **Raspberry Pi 3B+**
companion, no second computer on the airframe. Live weed-ID inference for the
≤150 ms spray loop stays on the **ground station** (see §3).

Masses and thrust figures below are **engineering estimates** for planning —
verify on a thrust stand and scales before chemical flight.

---

## 1. Locked choices

| Item | Choice |
|------|--------|
| Frame | **F450-class / Flamewheel 450** (≈450 mm motor-to-motor), lightweight X |
| Props | **8-inch, 3-blade** (matched CW/CCW set) |
| Motors (primary lock) | **4× 2216-class ~880–920 KV** on **4S** (fits F450 mounts; see thrust §5) |
| Motors (on hand) | **4× unused 980 KV**, **3S (11.1 V) compatible**, never flown — alternate power path; see **§5.1** |
| ESCs | 4× **30–40 A** BLHeli_S / AM32, 4-in-1 or discrete (confirm **3S-low** cutoffs if flying the 980 KV set) |
| Battery (4S path) | **4S LiPo 5000–6000 mAh** (≥25C); start **5000** for AUW |
| Battery (3S path) | **3S LiPo 5000–6000 mAh** (≥25C) when using the **980 KV** motors — **single 3S pack**, no 4S/6S conversion |
| FC | **Holybro Pixhawk 4** or **Cube Orange** (2 MB+ flash, sprayer + Lua) — **not** original Pixhawk1 / FMUv2 |
| Firmware | **ArduCopter** current stable, build **with `AC_Sprayer`** |
| RC | **ELRS 2.4 GHz** RX → FC `RCIN` / CRSF (or SBUS via ELRS PWM/CRSF bridge) |
| Companion | **Raspberry Pi 3B+** only (spare on hand) — serial/USB MAVLink to FC |
| Video | Thinker + IMX335 + RTL8812AU WFB-NG ([SYSTEM-SPEC.md](SYSTEM-SPEC.md)) — **not** through the Pi |
| Tank | **0.5 L** soft/rigid spray tank, CG on centreline (concentrate mix; see LIVE-PIPELINE dose) |
| Pump | Small **PWM** diaphragm / brushless pump on `SERVOn_FUNCTION = 22` |
| Boom | Light 3-section boom or single nozzle under CG ([LIVE-PIPELINE.md](LIVE-PIPELINE.md) §4.3) |
| RTK (air) | **Holybro H-RTK F9P Rover** (~**USD 205**, holybro.com) → Pixhawk GPS UART |
| RTK (ground) | **Holybro H-RTK F9P Base** (~**USD 259**) as **dedicated local base** — not third-party NTRIP |
| RTK pair cost | ~**USD 464–500** (rover + base; antennas as shipped; tripod extra) |
| OpenRTLS (optional) | **UWB** DWM1000-class anchors + air tag — see §6.1; does **not** replace RTK |

No hex conversion, no Jetson, no second Pi. RTK uses a **local Holybro base** so remote
paddocks without a nearby NTRIP mount still get centimetre fixes.


---

## 2. Flight controller — sprayer & ELRS (confirmed for this stack)

A **32-bit Pixhawk 4 / Cube Orange-class** board running ArduCopter with the
sprayer feature compiled in can:

| Need | How |
|------|-----|
| Drive pump | `SPRAY_ENABLE = 1`, `SERVOn_FUNCTION = 22` (pump), optional `23` spinner |
| Mission / GS pulse | **`MAV_CMD_DO_SPRAYER` (216)** param1 = 1/0 when supported in the build |
| Alternate enable | **`MAV_CMD_DO_AUX_FUNCTION`** aux **15** (Sprayer), or RC `RCx_OPTION = 15` |
| ELRS as RC | ELRS RX in **CRSF** (preferred) or SBUS into the FC; binds to ELRS TX — **hard authority** per INTEGRATION |

**Caveats:** many **1 MB flash** FCs omit sprayer and/or Lua — do **not** use
those. Confirm `SPRAY_*` params appear after `SPRAY_ENABLE = 1`. Cube Orange /
Pixhawk 4-class are the default picks here.

OA sensors (TFmini Plus down, SF45/B or forward TFminis) stay on the **FC**
([OBSTACLE-AVOIDANCE.md](OBSTACLE-AVOIDANCE.md)) — not on the Pi.

### 2.1 Original Pixhawk (FMUv2 / Pixhawk1) vs Pixhawk 4 vs Cube Orange

| | **Original Pixhawk** (STM32F427, FMUv2 / “Pixhawk 1”) | **Pixhawk 4** (FMUv5, STM32F765) | **Cube Orange** (H7) |
|--|--|--|--|
| Flash (typical) | Often **1 MB** (some later boards 2 MB) | **2 MB** | **2 MB** |
| RAM / CPU | **256 KB** SRAM, F4 @ ~168 MHz — tight under Copter + extras | More F7 headroom | **H7** — most headroom of the three |
| I/O | Classic DF13 set; fewer clean UARTs once GPS, telem, ELRS, Pi, lidars share ports | More serial / better connector set | Rich I/O; dual/isolate options on Cube carriers |
| ArduCopter + **AC_Sprayer** | **Only if** the published build still includes sprayer — **1 MB limited firmware often strips `AC_Sprayer`** ([Firmware Limitations](https://ardupilot.org/copter/docs/common-limited-firmware.html); same caveat in STACK / SETUP-NOTES) | Yes on normal Copter builds | Yes on normal Copter builds |
| **Lua** (RTL / tank-empty pump off) | Typically **no** on 1 MB scripting-disabled builds | Yes with `SCR_ENABLE` | Yes |
| **ELRS** as RC | Yes in principle (CRSF/SBUS into `RCIN` / a UART) — not an F427-specific block | Yes | Yes |
| MAVLink to **Pi 3B+** | Yes (TELEM2 / USB / UART) — serial bridging works on all three | Yes | Yes |
| OA + spray + telem + Pi together | Port and CPU/RAM contention; feature cuts are the real risk | Comfortable for this F450 stack | Comfortable; preferred if budget allows |

**Can the original Pixhawk run Copter with AC_Sprayer, ELRS, and MAVLink to the Pi?**
ELRS RC and a Pi MAVLink link are fine. **AC_Sprayer (and Lua shutoffs this
project relies on) are not reliable on classic 1 MB Pixhawk1 builds** — many
stock limited images omit sprayer entirely. A rare 2 MB FMUv2 board with a
custom full build is an edge case, not the plan.

**Recommendation:** **Do not use the original Pixhawk1 / FMUv2 for this spray
setup — step up to a Pixhawk 4 or Cube Orange** (Cube Orange if you want maximum
I/O and CPU margin; Pixhawk 4 is the acceptable minimal step-up).

---

## 3. Raspberry Pi 3B+ — role and YOLO reality

### 3.1 What the Pi **is** for (v1)

| Role | Notes |
|------|-------|
| MAVLink bridge | UART or USB to Pixhawk; `mavlink-router` / Rpanion / simple pymavlink |
| Logging | Onboard copy of spray/telem events to microSD |
| Optional NTRIP client | RTCM → FC if base/NTRIP path uses the Pi |
| Optional low-rate shadow ID | Offline-style ONNX on stills **≤ ~0.5–1 Hz**, **no** live spray authority |

### 3.2 Can it run YOLOv8n-seg ONNX at usable FPS for **live** weed ID?

**No — not for the locked live spray loop.**

| Constraint | Pi 3B+ reality |
|------------|----------------|
| CPU | Quad Cortex-A53 @ 1.4 GHz, **no neural accelerator** |
| RAM | **1 GB** total; after Raspberry Pi OS + networking often **≲ 600–700 MB** free |
| YOLOv8n-seg @ 640 ONNX | Expect roughly **≲ 0.5–2 FPS** CPU-only in practice (highly build-dependent); far above the LIVE-PIPELINE **~40 ms** infer budget |
| Thermal | Sustained ONNX → **throttling** without a heatsink/fan; FPS drops further |
| Live path | [LIVE-PIPELINE.md](LIVE-PIPELINE.md) needs **~15 Hz** ID with **≤ 150 ms** e2e — that runs on the **GS** (40-core / K80 path), not the Pi |

**Decision:** Pi 3B+ is the **single companion** for bridge/logging. **Authoritative
live weed ID + `DO_SPRAYER` pulses stay on the ground station.** Do not move the
conf 0.50 spray gate onto the Pi. Shadow ONNX on the Pi is optional R&D only.

---

## 4. Estimated all-up weight (AUW)

| Group | Est. mass |
|-------|-----------|
| F450 frame + hardware | 280–350 g |
| 4× 2216 motors | 200–240 g |
| ESCs + wiring | 80–120 g |
| 8×3 props (4) | 30–50 g |
| Pixhawk 4 / Cube + cables | 60–100 g |
| GPS / compass + mast | 40–60 g |
| ELRS RX + antenna | 5–15 g |
| OpenRTLS tag + ant (optional) | 5–20 g |
| Pi 3B+ + case + serial hat + SD | 70–100 g |
| Thinker + IMX335 + 8812AU + antennas | 40–80 g |
| 0.5 L tank empty + mounts | 80–150 g |
| Pump + hose + 3-section boom / nozzles | 200–350 g |
| Misc (pads, ties, OA lidars light kit) | 100–180 g |
| **Dry subtotal (no batt, no liquid)** | **~1.2–1.7 kg** |
| 4S 5000–6000 mAh pack (880–920 KV path) | 500–650 g |
| 3S 5000–6000 mAh pack (980 KV path) | often **~350–500 g** (weigh the actual pack) |
| **0.5 L mix** (concentrate) | **~500 g** |
| **Estimated wet AUW** | **~2.4–3.0 kg** (planning mid **~2.7 kg**) |

**Mix caveat:** concentrate must match **label rates**; mix **fresh per flight**
(many herbicides store poorly once diluted). First hover tests may still use
water only at 0.5 L to prove thrust before chemical.

---

## 5. Thrust margin — 8-inch 3-blade on F450

**Target:** wet AUW ≈ **2.7 kg** (0.5 L concentrate) → want **≥ 1.5:1** static
thrust (≥ **~4.0 kgf** total); **2:1** (~5.4 kgf) is healthier with slosh.

| Setup | Ballpark | Verdict |
|-------|----------|---------|
| Stock “toy” 2212 920 KV + light 8×4.5 two-blade | Often ~2.5–3.5 kgf total on 4S | Still **risky** at 2.7 kg wet |
| **Locked: 2216 ~880–920 KV + 8×3 three-blade on 4S** | Aim **≥ 1.1–1.3 kgf per motor** → **~4.5–5.2 kgf** quad | **Improved vs 1 L / ~3 kg** — about **1.7–1.9:1** at 2.7 kg if stand hits ~5 kgf; hover often **~45–55%** |
| Oversized 3508-class on F450 | High thrust | Still awkward on F450 mounts — not default |

**Decision:** keep **8×3**; **mandate a thrust-stand check** before chemical
flight. The **0.5 L** load is the planned wet mass — do not “top off to 1 L”
without re-checking margin. If total static thrust **&lt; 4.0 kgf**, step up
motor thrust bin or reduce other AUW — do **not** paper over a short margin.


### 5.1 On-hand motors — **980 KV on 3S** (unused set)

Caller has a **never-flown** set of **980 KV** motors specified **3S (11.1 V)
compatible**. Eligible as an **alternate** power path on the same F450 + **8×3**
props so the build can stay on a **single 3S LiPo** (lighter pack, simpler —
**no 4S/6S conversion**).

| Quantity | Value | Notes |
|----------|-------|-------|
| KV | **980** | As labelled on the unused set |
| Pack | **3S nominal 11.1 V** (≈12.6 V full charge) | Match ESC low-voltage cutoff to 3S |
| Theoretical max RPM (unloaded) | **980 × 11.1 ≈ 10,878 ≈ 10,900 RPM** | At full charge: **980 × 12.6 ≈ 12,350 RPM** — unloaded ceiling, not prop-loaded RPM |
| Props | Same locked **8-inch 3-blade** | Confirm motor shaft / hub fit before first spin |
| Electrical simplicity | **One 3S pack** | No series packs or buck/boost for the propulsion bus |

**Thrust (planning only — not measured on this set):**

Static thrust depends on **prop pitch, motor mass, ESC timing**, and cooling.
**KV × voltage alone cannot** give a trustworthy mid-throttle kilogram figure,
and ESC “50% stick” is **not** half RPM. Until a thrust-stand run exists for
*this* 980 KV + 8×3 + ESC combo:

- Do **not** invent a per-motor kgf at 50% throttle from theory.
- Keep the same gate as §5: combined static thrust should clear **≥ ~4.0 kgf**
  (1.5:1 at ~2.7 kg wet), preferably **≥ ~4.5 kgf** acceptance (§9).
- Expect **higher hover throttle** and **softer punch** than the locked
  **4S ~880–920 KV** path — **980 KV on 3S sits on the low-voltage / low-punch
  side** for snappy climbs, but can still suit a **slow, precise spot-spray
  loiter** if the stand clears margin.

**AUW side-effect:** a 3S 5000–6000 mAh pack is often **~150–250 g lighter**
than a similar 4S pack, which slightly helps the ~2.7 kg wet target — still
does not replace a stand test.

**Suitability verdict:** **Conditionally suitable** for this F450 touch-up
sprayer on 8×3 — good fit for a **gentle loiter / spot-spray** profile and a
**simple 3S** power system; **not** the punchier default versus 4S 880–920 KV.
**Fly only after** water-ballast hover and a recorded thrust-stand pass.

**8-inch on F450 with sprayer payload:** **more viable at ~2.7 kg wet** than at
the former ~3 kg / 1 L plan; still a **nimble touch-up** ship, not a heavy
ag mule. Extra ~300–500 g off the liquid is meaningful thrust margin on 8×3.

---

## 6. RTK GPS — Holybro F9P rover + local base

Locked for remote paddocks where **no nearby third-party NTRIP mount** exists.
Corrections come from **our own base**, independent of RTK2Go / CORS availability.

| Role | Product | Approx. price (USD) | Buy |
|------|---------|---------------------|-----|
| Air rover | **Holybro H-RTK F9P Rover** (ZED-F9P + compass; IST8310 or RM3100 SKU as ordered) | **~$205** | [holybro.com](https://holybro.com/products/h-rtk-f9p-rover) / ZED-F9P Rover listing |
| Local base | **Holybro H-RTK F9P Base** (survey antenna kit) | **~$259** | [holybro.com](https://holybro.com/products/h-rtk-f9p-base) |
| **Pair total** | Rover + base | **~$464–500** | Tripod / vehicle mount extra |

### Air — rover on the F450

- Mount the rover helix (or kit antenna) on a short mast **above** the props,
  clear of the Thinker 5 GHz antennas and ELRS 2.4 antennas.
- Connect to the **Pixhawk 4 / Cube Orange GPS (UART)** port with the supplied
  GH cable (GPS1 preferred). ArduPilot auto-detects u-blox F9P; enable RTK as
  in [ArduPilot RTK GPS Correction](https://ardupilot.org/copter/docs/common-rtk-correction.html).
- Inject RTCM from the base via **GCS telemetry** (Mission Planner / QGC RTK
  Inject) or a dedicated radio link — **not** over WFB-NG video RF
  ([INTEGRATION.md](INTEGRATION.md)).

### Ground — dedicated F9P base

- Place the base on a **survey tripod** or **vehicle roof** at the **paddock
  edge** (clear sky, stable, away from metal roofs when possible).
- Survey-in or use a known fixed position; leave powered for the sortie.
- Expected horizontal accuracy with a solid RTK Fix: about **1–2 cm**
  (module class is ~1 cm + 1 ppm; treat **1–2 cm** as the ops planning figure
  once Fix is green).
- Rationale for owning the base: remote AU paddocks often have **no usable
  nearby NTRIP mount**; the Holybro pair is self-contained.

### Mass note

Rover module + antenna is already covered under “GPS / compass + mast” in the
AUW table (~40–60 g module class; helix adds a little — weigh the actual SKU).

---

## 6.1 OpenRTLS (optional) — UWB precision near trees / structures

**Status:** optional add-on, not a v1 lock. RTK (Holybro F9P) remains the primary
outdoor centimetre source. OpenRTLS (also spelled Open-RTLS; open-source UWB RTLS
families such as [UWB-Open-RTLS-MCU](https://github.com/demianzenkov/UWB-Open-RTLS-MCU)
/ POSIT.PRO-class stacks) fills gaps where sky view is poor — tree lines, sheds,
tank stands — and RTK Fix drops to float or single.

Caller also said **“and also”** — leave a slot for a later follow-up item under
this same radios / precision heading (do not treat OpenRTLS as the last word).

### What it is

| Piece | Role |
|-------|------|
| **Anchors** (≥ **4**) | Fixed Decawave/Qorvo **DWM1000**-class UWB nodes around the work zone; survey their positions once |
| **Tag** (1 on airframe) | Mobile UWB node; TWR/TDOA ranging against anchors |
| **Solver** | Anchor Ethernet/USB → small PC or **Pi on the GS** runs the OpenRTLS / POSIT-style server; tag pose out at ~10 Hz class |
| **Band** | UWB (~3.5–6.5 GHz class for DW1000) — keep antennas clear of Thinker **5 GHz** WFB and ELRS **2.4** |

Typical published figures for this class of kit: ranging on the order of **tens of metres
per hop** (often cited ~**30 m** line-of-sight for DWM1000 DIY), position error down
toward **~10 cm** in good TWR geometry. That is **not** whole-paddock coverage with
four anchors on a 5-acre block — place the tetrahedron (or more anchors) around the
**degraded-GPS corridor** (tree belt, barn edge), not as a RTK replacement for open sky.

### Minimal cost (AliExpress DWM1000 DIY)

| Item | Qty | Approx. each (USD) | Subtotal |
|------|-----|--------------------|----------|
| DWM1000 breakout / cheap STM32+DWM1000 node | **4** anchors + **1** tag | **~$15–25** | **~$75–125** |
| USB power / Ethernet dongles / cases / masts | — | — | **~$25–75** |
| **Minimal kit ballpark** | | | **~$100–200** |

PoE industrial anchors and a dedicated RTLS server push cost up; the figures above
are the **cheap AliExpress module** path for trials. Weigh the air tag (~few grams
to ~20 g with antenna) into AUW when fitted.

### How position reaches the Pixhawk (alongside RTK)

Preferred v1 path (fits the existing **Pi 3B+** companion role):

1. Anchors → GS (or paddock-edge) OpenRTLS server computes tag XYZ in a local frame
   surveyed relative to the RTK base / paddock origin.
2. Air **tag** UART/USB → **Pi 3B+** (or GS forwards pose over **SiK / ELRS telem**).
3. Pi (or GS hub) emits MAVLink **`VISION_POSITION_ESTIMATE`** (or **`GPS_INPUT`** /
   external-nav equivalent) into the FC.
4. ArduPilot **EKF3** fuses UWB with the F9P RTK when both are healthy; when RTK
   degrades near canopy, raise UWB innovation weight / use as horizontal aid only
   after a bench tune — **never** let UWB override ELRS or kill RC authority.

**Do not** send OpenRTLS traffic over WFB-NG video RF. Keep it on UART + telem /
Ethernet like RTCM and MAVLink.

### Acceptance when fitted

1. Four anchors surveyed; tag range checks to each anchor in open air.  
2. SITL or bench: `VISION_POSITION_ESTIMATE` (or chosen msg) appears in MAVProxy.  
3. Hover under partial sky: RTK float + UWB still yields usable horizontal for
   georeg logging (spray gate still needs LIVE-PIPELINE georeg rules).

---

## 7. Power budget (approximate)

| Load | Voltage | Current / power (order of magnitude) |
|------|---------|--------------------------------------|
| Hover (~2.7 kg, efficient 8×3) | 4S pack (880–920 KV path) | **~22–35 A** pack draw (tune after hover tests) |
| Hover (~2.7 kg, 8×3) | **3S pack (980 KV path)** | Expect **higher % throttle** for same AUW; measure pack amps on hover — do not copy 4S numbers |
| Cruise / climb | 4S | **40–70 A** peaks — size ESC/batt for bursts |
| Cruise / climb | **3S** | Softer voltage headroom — climbs will be **less snappy**; size ESC/batt after stand + hover |
| Pixhawk + peripherals | 5 V BEC | **~0.3–0.6 A** |
| Pi 3B+ (idle bridge) | 5 V (≥2.5 A supply) | **~0.4–0.7 A**; **1–1.5 A** if thrashing ONNX |
| Thinker + 8812AU TX | 2–6S / BEC per OpenIPC | Follow SYSTEM-SPEC; treat as **several watts to tens of watts** at legal TX power |
| ELRS RX | 5 V | **&lt; 0.2 A** |
| OpenRTLS air tag (if fitted) | 5 V / Lipo | **&lt; 0.2 A** typical |
| Spray pump (pulsed) | Pack or BEC | Bench-measure; budget **2–8 A** while on |
| OA lidars | 5 V | **&lt; 0.3 A** combined |

**Battery:** 4S **5000–6000 mAh** → rough **8–15 min** mixed hover/touch-up if
average ~30 A (very duty-cycle dependent). Land with **≥ 20–25%** remaining.
Power the Pi from a **dedicated 5 V / ≥3 A** BEC or FC rail known to hold up —
brownouts corrupt SD and drop MAVLink.

---

## 8. Wiring sketch (minimal)

```
ELRS TX (pilot)
    │ 2.4 GHz CRSF/SBUS
    ▼
Pixhawk 4 / Cube (Copter + AC_Sprayer + OA)
    ├─ GPS1 UART ──► Holybro H-RTK F9P Rover (air)
    ├─ UART/USB ──► Pi 3B+ (mavlink-router / logs only)
    ├─ SERVO  pump FUNCTION 22
    ├─ RC option 15 sprayer inhibit/enable
    ├─ TFmini / SF45 as OA docs
    └─ telem ──► GS (SiK or ELRS telem)
           ← live ID & voice COMMAND_LONG
           ← RTCM from Holybro H-RTK F9P Base (paddock edge)

Holybro F9P Base ──(tripod / vehicle roof)──► GCS RTK Inject ──telem──► FC

Thinker video ──WFB-NG 5 GHz──► GS decode  ← NOT via Pi

OpenRTLS (optional): 4× DWM1000 anchors ──Ethernet/USB──► GS solver
                     air tag ──UART──► Pi ──VISION_POSITION_ESTIMATE──► FC
                     (fuse with RTK in EKF3; not over WFB-NG)
```

---

## 9. Acceptance checks before spray

1. Scales: dry and wet AUW recorded.  
2. Thrust stand: 4 motors ≥ **4.5 kgf** combined at usable temperature (same gate for **4S 880–920 KV** or **3S 980 KV** paths).  
3. Hover water-only at **0.5 L** (ops fill), props clear of boom.  
4. `DO_SPRAYER` / aux 15 from GS hub with Pi bridging telem (pump on bench).  
5. Confirm Pi CPU stays cool in bridge-only mode; **no** live-spray ONNX on Pi.  
6. ELRS failsafe → pump off (Lua / params per SETUP-NOTES).
7. RTK: base survey-in at paddock edge; rover shows **RTK Fixed**; confirm
   1–2 cm class before trusting live georeg / waypoint revisits.
8. If OpenRTLS fitted: four anchors surveyed; UWB pose reaches FC via Pi/telem;
   confirm fusion under tree-line shade without fighting ELRS.

---

## 10. One-paragraph summary

v1 airframe is a **nimble F450 / Flamewheel 450** on **8-inch 3-blade** props
with **2216-class ~880–920 KV on 4S** as the primary power lock, or the
**on-hand unused 980 KV motors on a single 3S pack** as a simpler/lighter
alternate (theoretical max ~**10,900 RPM** at 11.1 V; stand-test before
chemical — fine for slow spot-spray loiter if thrust margin clears, softer
than 4S for punchy climbs), a **Pixhawk 4 or Cube Orange** FC (not original Pixhawk1) running
ArduCopter **`AC_Sprayer`** (`DO_SPRAYER` / aux **15**) and **ELRS** RC, plus a
single **Pi 3B+** for MAVLink/logging only — **not** for live YOLOv8n-seg (CPU
/~1 GB RAM cannot meet the GS live-ID FPS/latency budget) — at roughly
**~2.7 kg wet AUW** with a **0.5 L** concentrate tank (improved 8×3 thrust
margin vs the old 1 L / ~3 kg plan; stand-test still mandatory), **Holybro
H-RTK F9P Rover + local F9P Base** (~USD 464–500) for 1–2 cm RTK without
NTRIP, with **optional OpenRTLS (DWM1000 UWB)** as a local centimetre aid near
trees/structures where RTK degrades (~$100–200 minimal 4-anchor+tag kit), keeping
the stack to one FC and one companion.

---

## Related

- [INTEGRATION.md](INTEGRATION.md) · [STACK.md](STACK.md) · [SYSTEM-SPEC.md](SYSTEM-SPEC.md) ·
  [LIVE-PIPELINE.md](LIVE-PIPELINE.md) · [OBSTACLE-AVOIDANCE.md](OBSTACLE-AVOIDANCE.md) ·
  [SETUP-NOTES.md](SETUP-NOTES.md)
