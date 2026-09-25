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
| Motors (decision) | **4× 2216-class ~880–920 KV** on **4S** (fits F450 mounts; see thrust §5) |
| ESCs | 4× **30–40 A** BLHeli_S / AM32, 4-in-1 or discrete |
| Battery | **4S LiPo 5000–6000 mAh** (≥25C); start **5000** for AUW |
| FC | **Holybro Pixhawk 4** or **Cube Orange** (2 MB+ flash, sprayer + Lua) — **not** original Pixhawk1 / FMUv2 |
| Firmware | **ArduCopter** current stable, build **with `AC_Sprayer`** |
| RC | **ELRS 2.4 GHz** RX → FC `RCIN` / CRSF (or SBUS via ELRS PWM/CRSF bridge) |
| Companion | **Raspberry Pi 3B+** only (spare on hand) — serial/USB MAVLink to FC |
| Video | Thinker + IMX335 + RTL8812AU WFB-NG ([SYSTEM-SPEC.md](SYSTEM-SPEC.md)) — **not** through the Pi |
| Tank | **1 L** soft/rigid spray tank, CG on centreline |
| Pump | Small **PWM** diaphragm / brushless pump on `SERVOn_FUNCTION = 22` |
| Boom | Light 3-section boom or single nozzle under CG ([LIVE-PIPELINE.md](LIVE-PIPELINE.md) §4.3) |

No hex conversion, no Jetson, no second Pi.

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
| Pi 3B+ + case + serial hat + SD | 70–100 g |
| Thinker + IMX335 + 8812AU + antennas | 40–80 g |
| 1 L tank empty + mounts | 120–200 g |
| Pump + hose + 3-section boom / nozzles | 200–350 g |
| Misc (pads, ties, OA lidars light kit) | 100–180 g |
| **Dry subtotal (no batt, no liquid)** | **~1.2–1.8 kg** |
| 4S 5000–6000 mAh pack | 500–650 g |
| **1 L water / mix** | **~1000 g** |
| **Estimated wet AUW** | **~2.7–3.4 kg** (planning mid **~3.0 kg**) |

Touch-up with a **partial fill** (e.g. 0.3–0.5 L) is recommended for first
flight tests.

---

## 5. Thrust margin — 8-inch 3-blade on F450

**Target:** wet AUW ≈ **3.0 kg** → want **≥ 1.5:1** static thrust (≥ **4.5 kgf**
total) for controllable spray; **2:1** (6 kgf) is healthier with liquid slosh.

| Setup | Ballpark | Verdict |
|-------|----------|---------|
| Stock “toy” 2212 920 KV + light 8×4.5 two-blade | Often ~2.5–3.5 kgf total on 4S | **Insufficient** for 3 kg wet |
| **Locked: 2216 ~880–920 KV + 8×3 three-blade on 4S** | Aim **≥ 1.1–1.3 kgf per motor** on stand → **~4.5–5.2 kgf** quad | **Marginal but workable** if stand-tested; hover ~50–65% |
| Oversized 3508-class on F450 | High thrust | Often **mechanically awkward** (mounts, arms, CG) — not default |

**Decision:** keep **8×3** as requested; **mandate a thrust-stand check** before
any full 1 L flight. If total static thrust **&lt; 4.5 kgf**, either (a) fly
**≤ 0.5 L**, (b) step to a higher-thrust 2216/2306 bin on 4S, or (c) accept a
larger frame later — do **not** paper over a short thrust margin.

**8-inch on F450 with sprayer payload:** **viable only with adequate 2216-class
thrust and careful AUW** (partial tank, light boom). It is **not** a comfortable
ag-hex margin; treat as a **nimble touch-up** ship, not a heavy spray mule.

---

## 6. Power budget (approximate)

| Load | Voltage | Current / power (order of magnitude) |
|------|---------|--------------------------------------|
| Hover (3 kg, efficient 8×3) | 4S pack | **~25–40 A** pack draw (tune after hover tests) |
| Cruise / climb | 4S | **40–70 A** peaks — size ESC/batt for bursts |
| Pixhawk + peripherals | 5 V BEC | **~0.3–0.6 A** |
| Pi 3B+ (idle bridge) | 5 V (≥2.5 A supply) | **~0.4–0.7 A**; **1–1.5 A** if thrashing ONNX |
| Thinker + 8812AU TX | 2–6S / BEC per OpenIPC | Follow SYSTEM-SPEC; treat as **several watts to tens of watts** at legal TX power |
| ELRS RX | 5 V | **&lt; 0.2 A** |
| Spray pump (pulsed) | Pack or BEC | Bench-measure; budget **2–8 A** while on |
| OA lidars | 5 V | **&lt; 0.3 A** combined |

**Battery:** 4S **5000–6000 mAh** → rough **8–15 min** mixed hover/touch-up if
average ~30 A (very duty-cycle dependent). Land with **≥ 20–25%** remaining.
Power the Pi from a **dedicated 5 V / ≥3 A** BEC or FC rail known to hold up —
brownouts corrupt SD and drop MAVLink.

---

## 7. Wiring sketch (minimal)

```
ELRS TX (pilot)
    │ 2.4 GHz CRSF/SBUS
    ▼
Pixhawk (Copter + AC_Sprayer + OA)
    ├─ UART ──► Pi 3B+ (mavlink-router / logs only)
    ├─ SERVO  pump FUNCTION 22
    ├─ RC option 15 sprayer inhibit/enable
    ├─ GPS / TFmini / SF45 as OA docs
    └─ telem ──► GS (SiK or ELRS telem)  ← live ID & voice COMMAND_LONG

Thinker video ──WFB-NG 5 GHz──► GS decode  ← NOT via Pi
```

---

## 8. Acceptance checks before spray

1. Scales: dry and wet AUW recorded.  
2. Thrust stand: 4 motors ≥ **4.5 kgf** combined at usable temperature.  
3. Hover water-only at **0.5 L**, then **1 L**, props clear of boom.  
4. `DO_SPRAYER` / aux 15 from GS hub with Pi bridging telem (pump on bench).  
5. Confirm Pi CPU stays cool in bridge-only mode; **no** live-spray ONNX on Pi.  
6. ELRS failsafe → pump off (Lua / params per SETUP-NOTES).

---

## 9. One-paragraph summary

v1 airframe is a **nimble F450 / Flamewheel 450** on **8-inch 3-blade** props
with **2216-class 4S** power, a **Pixhawk 4 or Cube Orange** FC (not original Pixhawk1) running
ArduCopter **`AC_Sprayer`** (`DO_SPRAYER` / aux **15**) and **ELRS** RC, plus a
single **Pi 3B+** for MAVLink/logging only — **not** for live YOLOv8n-seg (CPU
/~1 GB RAM cannot meet the GS live-ID FPS/latency budget) — at roughly
**~3 kg wet AUW** with a **1 L** tank, where 8×3 thrust is **marginal and
stand-test mandatory**, keeping the stack to one FC and one companion.

---

## Related

- [INTEGRATION.md](INTEGRATION.md) · [STACK.md](STACK.md) · [SYSTEM-SPEC.md](SYSTEM-SPEC.md) ·
  [LIVE-PIPELINE.md](LIVE-PIPELINE.md) · [OBSTACLE-AVOIDANCE.md](OBSTACLE-AVOIDANCE.md) ·
  [SETUP-NOTES.md](SETUP-NOTES.md)
