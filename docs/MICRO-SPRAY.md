# Micro-spray variant — S500 dart platform

Lightweight alternate airframe for the same **~20–25 weeds / ~5-acre** touch-up
mission. Complements the primary F450 / 0.5 L stack in [AIRFRAME.md](AIRFRAME.md).
**Ground-station software is unchanged** — only airframe and sprayer hardware
differ. Compiled 2026-09-26.

**Status:** engineering variant spec (planning estimates). Verify thrust, flow,
and AUW on the stand before chemical flight. Does **not** replace the F450
primary lock unless this build proves out.

---

## 0. Concept (locked)

| Item | Choice |
|------|--------|
| Role | Fast **dart** between sparse weeds: cruise hard, brief hover, pulse, move on |
| Frame class | **S500 / SK500 (~480–500 mm)** PCB or carbon arms — **not** a pure 5″ race freestyle |
| Battery | **4S LiPo 1800–2200 mAh** (≥45C); carry spares for multi-pack sorties |
| Tank | **150 mL** primary (range **100–200 mL**); soft bottle or 3D-printed bottle mount on CG |
| Pump | Mini **12 V peristaltic** (Kamoer KEF / KXF class) preferred; cheap diaphragm OK for water bench |
| Nozzle | **Single** centreline mist / flat-fan under CG (dual optional later) |
| Motors | Reuse on-hand **980 KV** on **4S** (their typical rating) with **10×4.5** (or similar) props |
| ESC | **30–45 A 4-in-1** BLHeli_32 / AM32 / modern BLS, 3–6S, ~**USD 20–40** |
| Mission math | Same dose \(V=\mathrm{clamp}(0.25+1.25 A_{\mathrm{cm}^2}, 0.1, 2.5)\) mL — ~25×2.5 mL ≈ **62 mL** worst case → 150 mL tank has margin |

**Naming note:** “5-inch racing-style” in conversation maps here to a **small,
agile multirotor**, not a 220 mm freestyle chassis. A true 5″ FPV frame cannot
reliably carry **300–500 g** of tank + pump + lines and still keep GPS/RTK/video
gear. **S500** is the lock: still much lighter and quicker in transit than a
loaded F450 with 0.5 L, closer in size to the user’s F450 mounts, and happy
with the on-hand 980 KV motors on 4S + 10″ props.

---

## 1. Frame — payload capacity

| Option | Diagonal | Extra payload ballpark | Verdict |
|--------|----------|------------------------|---------|
| Pure 5″ freestyle (True-X / deadcat) | ~220 mm | ~100–200 g camera-class | **Reject** for 300–500 g spray kit |
| **S500 / SK500** | ~480–500 mm | **~400–800 g** practical with thrust margin | **Lock** |
| 7″ long-range | ~280–300 mm props | High | Optional upgrade if S500 thrust fails wet |
| F450 (primary) | ~450 mm | Designed for 0.5 L | Keep as endurance / chemical primary |

**Decision:** buy or build an **S500-class** kit (~**USD 30–40** frame-only on
AliExpress; ~**USD 65–80** if bundled with cheap motors/ESCs — prefer buying
frame alone and using known motors/ESCs). Reinforced arms or carbon landing
gear preferred. Mount the tank on the **centre plate** (CG), pump aft or under
the plate, single nozzle on a short boom under CG.

---

## 2. Motors — 980 KV on 4S

**Yes — the on-hand 980 KV set is intended for 4S** (and often 3S–4S; some clones
claim 2–4S only). For this micro variant:

| Item | Lock |
|------|------|
| Voltage | **4S only** on this airframe (do **not** run 6S on S500 + 10″ without a thrust/current re-check) |
| Props | **10×4.5** (or 1045) CW/CCW — classic S500 pairing |
| Use | Flight-test / dart micro-spray; primary F450 path remains 2216 ~880–920 KV or documented 6S path per AIRFRAME §5.1 |

Unlabeled “980 KV” clones may still be **2–4S only** — confirm ESC and motor
markings, and never exceed the lower of motor/ESC cell ratings. Theoretical
unloaded RPM on 4S ≈ \(980 \times 14.8 \approx 14{,}500\) RPM — appropriate for
10″, not for 5″ race props.

If the 980s prove weak at wet AUW, step to **2212/2216 ~920 KV** on 4S (cheap
S500 kit motors) rather than raising voltage.

---

## 3. ESCs

| Option | Spec | Approx. price (USD) | Notes |
|--------|------|---------------------|-------|
| **Generic / SpeedyBee-class 4-in-1** | **30–45 A**, BLHeli_32 / AM32 / BLS, 3–6S | **20–35** | **Preferred** for S500 stack under FC |
| SpeedyBee BLS 50A 30×30 | 50 A ×4, 3–6S | ~35–41 | Extra headroom |
| Discrete Hobbywing XRotor 40A ×4 | 2–6S | ~13–20 each (52–80 set) | Heavier; fine if already owned |

**Decision:** one **30–45 A 4-in-1** (~**USD 25–35**) on 30×30 under the FC.
Confirm **4S** (or 3–6S) on the silkscreen. SimonK firmware is obsolete — skip.

---

## 4. Micro pump

| Type | Example | Flow | AliExpress / web ballpark | Verdict |
|------|---------|------|---------------------------|---------|
| **Peristaltic 12 V** | Kamoer **KEF / KXF** | ~**1–85** or **3–80 mL/min** (tube/voltage dependent) | ~**USD 15–25** | **Lock** — self-priming, precise pulse dosing, herbicide-friendly tubing (use chemical-rated tube) |
| Peristaltic stepper | Kamoer KCM | higher precision | ~70–95 | Overkill / heavy for micro |
| Mini diaphragm 12 V | generic self-priming | often **0.5–2+ L/min** (too coarse unless PWM-gated hard) | ~**USD 5–15** | OK for **water bench**; harder to meter 0.1–2.5 mL pulses |

**Decision:** **Kamoer KEF or KXF 12 V** (or equivalent generic peri) at
~**USD 18–25**. Drive from Pixhawk `SERVOn_FUNCTION = 22` via a small MOSFET /
BEC (pump is 12 V; craft is 4S ≈ 14.8 V — use a **12 V regulator** or a pump
rated for 6–14 V and calibrate). Bench \(Q\) in mL/s at the PWM used in flight;
recompute \(t_{\mathrm{on}}\) from LIVE-PIPELINE §4.2.

At 40 mL/min ≈ 0.67 mL/s, a 2.5 mL pulse needs ~3.7 s — **too slow** for a
dart hover. So either:

1. Use a higher-flow tube set / duty so \(Q \approx 6\text{–}15\) mL/s for
   \(t_{\mathrm{on}}\) in the locked **0.04–0.40 s** window, **or**
2. Accept longer hover (0.5–2 s) on micro and widen \(t_{\mathrm{on}}\) clamp in
   software for this airframe only (config flag — same gate logic).

**Lock for v1 micro:** calibrate toward **\(Q \ge 6\) mL/s** at operating PWM
(diaphragm gated hard, or peri with larger ID tube / higher RPM model such as
Kamoer ~100 mL/min class). Keep dose \(V\) identical; only \(t_{\mathrm{on}}\)
and tank capacity params change.

---

## 5. Nozzle and droplets

| Choice | Detail |
|--------|--------|
| Count | **Single** centreline nozzle under CG (simplest CG and plumbing) |
| Dual | Optional later for a wider spot; not required at ~25 weeds |
| Tip | Small **flat-fan** or agricultural mist tip (TeeJet-compatible XR/TP class or cheap AliExpress misting nozzle ~**USD 2–8**) |
| Droplet target | **Medium–coarse** for foliar systemic herbicide — roughly **~226–400 µm** VMD guidance; avoid ultra-fine fog (&lt;150 µm) for drift over pasture |
| Height | Same spray AGL band as primary (~5–15 m ops intent; micro may fly **lower** on dart — re-tune look-ahead) |

Ultra-fine “misting” tips look spray-like on video but **drift** and burn
non-target pasture with concentrate. Prefer a tip the label would call medium
or coarse; verify with water-sensitive paper on the bench.

---

## 6. Battery and flight time

| Pack | Energy (approx.) | Role |
|------|------------------|------|
| **4S 1800–2200 mAh** ≥45C | ~27–33 Wh | **Lock** for dart sorties |
| 4S 3000–4000 mAh | heavier | Optional if hover time short |

### 6.1 AUW estimate (planning)

| Group | Est. mass |
|-------|-----------|
| S500 frame + gear | 280–400 g |
| 4× 980 KV + 10″ props | 200–280 g |
| 4-in-1 ESC + wiring | 40–80 g |
| Pixhawk 4 / Cube + GPS + ELRS | 100–160 g |
| Pi 3B+ (if carried) | 70–100 g — **optional on micro** if telem bridge stays on GS radio alone; prefer keep Pi for log parity |
| Video (Thinker path) | 40–80 g |
| Tank empty + pump + nozzle + lines | **150–280 g** |
| **150 mL** mix | **~150 g** |
| 4S 2200 mAh | ~200–250 g |
| Misc / OA light | 50–100 g |
| **Estimated wet AUW** | **~1.2–1.7 kg** (planning mid **~1.4 kg**) |

### 6.2 Hover / mission time (estimate)

Assume ~**5–7 g/W** hover efficiency on 10″ / S500, AUW 1.4 kg → hover power
roughly **200–280 W**. Usable energy from 4S 2200 mAh ≈ \(14.8 \times 2.2 \times 0.8 \approx 26\) Wh.

| Mode | Ballpark |
|------|----------|
| Pure hover | **~6–10 min** |
| Dart mission (transit + brief hovers) | **~4–8 min** useful per pack |
| Full 25-weed paddock | Likely **2–4 packs** or a mid-field swap — still fine for sparse touch-up |

These are **estimates** — weigh the finished craft and log amp-hours on water
flights before chemical.

---

## 7. Cost summary (airframe + spray rig only)

Shared stack (Pixhawk / Cube, Pi, ELRS, Thinker video, Holybro RTK) is **not**
re-counted — assume moved between airframes or duplicated later.

| Line | Approx. USD |
|------|-------------|
| S500 frame kit | 30–40 |
| Motors | **0** if reuse 980 KV; else 40–80 |
| 4-in-1 30–45 A ESC | 20–35 |
| Props (2–3 sets) | 10–20 |
| 4S 2200 mAh (×2 packs recommended) | 50–80 |
| 150 mL tank + fittings | 10–20 |
| Peristaltic pump (Kamoer-class) | 15–25 |
| Nozzle + tubing (chem-rated) | 5–15 |
| 12 V reg / MOSFET / mounts / misc | 15–30 |
| **Total incremental (reuse motors, 2 packs)** | **~USD 155–265** |
| **Total if buying motors too** | **~USD 195–345** |

Round planning figure: **~USD 200–280** for a complete micro airframe + spray
rig with motors reused and two flight packs.

---

## 8. Software — same GS stack

| Module | Change for micro? |
|--------|-------------------|
| `mav_node` / telem bridge | **No** — same MAVLink |
| `safety_gate` | **No** — same conf ≥ 0.50, georeg ≥ 0.70, ELRS authority, fail-closed |
| `mavlink_hub` | **No** — still sole `MAV_CMD_DO_SPRAYER` writer |
| `spray_mission` / LIVE-PIPELINE dose | **Params only:** tank capacity **0.15 L** (or 0.10–0.20); optional wider \(t_{\mathrm{on}}\) clamp if pump \(Q\) is low |
| WAYPOINT-OPT / approach-scan | **No** — same clustering; micro benefits from **higher cruise** between clusters |
| Ollama agent | **No** — advisory only |
| ArduCopter | Same `AC_Sprayer`; frame type **QUAD**; tune PID / ATC for S500 |

**Decision:** one software product, two airframes. Select airframe profile via
config (`airframe: f450 | micro_s500`) for tank litres, pump \(Q\), and
\(t_{\mathrm{on}}\) clamps — never fork the gate logic.

---

## 9. Trade-offs vs F450 / hex-class primary

| | **Micro S500** | **F450 primary (0.5 L)** |
|--|----------------|---------------------------|
| Transit between weeds | **Much faster** | Slower, more deliberate |
| Redundancy | Quad — **one motor loss = crash** | Still quad on F450; hex not in v1 lock |
| Flight time / pack | **Shorter** (4–8 min dart) | Longer with 4S 5–6 Ah |
| Chemical capacity | **150 mL** (enough for ~25 weeds at locked dose) | **500 mL** margin / denser days |
| Payload / sensors | Tight — OA kit may be minimal | Room for boom + lidars |
| Cost (incremental) | **~USD 200–280** | Higher tank/boom/propulsion |
| Best use | Scout-follow **dart** touch-up, training, pump R&D | Primary chemical ops |

**When to fly micro:** sparse paddocks, short sorties, validating pulse timing,
or when the F450 is down. **When to fly F450:** wind, denser patches, full OA
kit, or any flight where ballast and endurance matter more than dash speed.

---

## 10. Open benches before chemical

1. Dry AUW on scales; wet with 150 mL water.
2. Thrust stand: total static ≥ **~2.1–2.8 kgf** (≥1.5:1 at 1.4–1.7 kg).
3. Pump flow \(Q\) at flight PWM; confirm \(t_{\mathrm{on}}\) for 0.1–2.5 mL.
4. Droplet card under hover — medium/coarse, not fog.
5. One water mission of 10 simulated weeds before herbicide.

---

## Related docs

- [AIRFRAME.md](AIRFRAME.md) — primary F450 lock
- [LIVE-PIPELINE.md](LIVE-PIPELINE.md) — dose, gates, approach-scan
- [INTEGRATION.md](INTEGRATION.md) — ELRS authority, mavlink_hub
- [WAYPOINT-OPT.md](WAYPOINT-OPT.md) — cluster routing
