# Precision spot-spray drone — presentation overview

**Audience:** non-technical and semi-technical stakeholders  
**Source:** project docs in this repo (AIRFRAME, LIVE-PIPELINE, INTEGRATION, SYSTEM-SPEC, GAPS, drone_stack).  
**Status of this file:** briefing summary — not a flight manual and not a vendor quote.

---

## 1. Project overview

We are building a small autonomous **spot-spray** drone for **touch-up** work on already-clean paddocks — roughly **twenty-five weeds on about five acres**, not blanket spraying. The aircraft is a cheap **F450-class** hobby frame with an **eight-inch, three-blade** prop setup. The **brains for weed spotting** live on a Linux **ground station** (camera feed in, spray decisions out). The flight controller flies the aircraft; a cheap Raspberry Pi on the airframe only bridges messages and logs. Everything that can spray, arm, or fly is designed to **fail closed**: if video, GPS quality, radio, or confidence is bad, the pump stays off and the pilot’s radio remains in charge.

---

## 2. Hardware breakdown

| Piece | What it is / why | Notes from docs |
|-------|------------------|-----------------|
| **F450-class frame** | ~450 mm hobby “X” quad | Light touch-up ship, not a big ag mule |
| **8-inch 3-blade props** + 2216-class motors on **4S** | Thrust for ~**2.7 kg** wet all-up weight | Thrust-stand check mandatory before chemical |
| **Pixhawk 4** or **Cube Orange** | Flight computer running ArduPilot Copter | **Not** old Pixhawk1 (too little flash for sprayer features) |
| **Raspberry Pi 3B+** | Onboard companion | **MAVLink bridge and logs only** — not live weed ID |
| **Holybro H-RTK F9P rover + local F9P base** | Centimetre GPS for remote paddocks | Pair ~**USD 464–500** (docs); own base, not shared internet RTK |
| **ELRS 2.4 GHz RC** (diversity RX preferred) | Pilot sticks, arm, kill, sprayer inhibit | **Hard authority** — always wins over software |
| **WFB-NG 5 GHz video** (RTL8812AU, diversity on GS) | Live camera to ground station | Video only — never carries stick or spray commands |
| **SiK / telemetry radio** (or ELRS telem) | Aircraft status and spray commands | Separate from the video radio |
| **OpenIPC Thinker + IMX335** | Air camera | Locked video path in SYSTEM-SPEC |
| **Optional OpenRTLS UWB** | Extra position near trees / sheds | Optional; DIY kit ballpark ~**USD 100–200** |
| **Pump + boom + nozzles** | Spot pulses under the aircraft | Exact pump/nozzle/boom SKUs still a **gap** (see GAPS) |
| **0.5 L concentrate tank** | Half-litre tank, stronger mix, less water weight | Improves thrust margin vs an old 1 L plan |

**Approximate cost:** the docs do **not** contain a full locked bill of materials with a single airframe total. What *is* priced: RTK rover+base about **USD 464–500**; optional UWB trial kit about **USD 100–200**. Camera/radio alternatives elsewhere in the notes have individual retail figures, but frame, flight controller, motors, ELRS, video cards, pump, and ground PC are **not** summed into one number here — treat a complete build budget as **to be quoted from a shopping list**, not invented.

**Rough mass:** planning wet weight about **2.7 kg** with the half-litre tank; want at least about **1.5:1** static thrust (healthier near **2:1**).

---

## 3. Software stack

Plain-language picture of who does what:

```
Pilot (ELRS transmitter) ── hard control / arm / inhibit ──► Pixhawk (ArduPilot Copter)
                                                              │
Ground Linux PC (Mint/Ubuntu)                                 │
  QGroundControl  ··· mission view / plans ···················┤
  MAVProxy        ··· radio/UDP router ·······················┤
  mav_node        ··· telemetry record + QGC plan files ·····─┤
  safety_gate     ··· deterministic allow/deny (no AI) ·······┤
  spray_mission   ··· weed map → waypoints + dose math ·······┤
  mavlink_hub     ··· ONLY module that may send spray cmds ···┤
  live YOLO-seg   ··· see weeds in the live camera (GS GPU/CPU)┤
  voice layer     ··· optional spoken mode changes (confirm) ·┤
  Ollama agent    ··· advisory chat only — never flies ·······┘
         ▲
   SITL / HIL / bench pump tests before any field chemical use
```

- **ArduPilot Copter** on the Pixhawk flies the vehicle and can drive the sprayer hardware.  
- **QGroundControl** on Linux is the map and mission screen (no Windows Mission Planner dependency for this build).  
- **MAVProxy** sits between the radio link and the tools.  
- **drone_stack** middleware (`mav_node`, `safety_gate`, `spray_mission`, `mavlink_hub`, optional **Ollama**) matches the locked safety and dose rules.  
- **Live YOLO segmentation** runs on the **ground station**, not on the Pi.  
- **Voice control** is a convenience layer with read-back and confirm; it does not replace the transmitter.  
- **SITL → HIL → bench pump** is the simulation ladder before field spray.

---

## 4. Operational workflow

Step by step, from empty paddock knowledge to a careful spray pass:

1. **Map flight** — fly a survey pattern; build an orthomosaic (map photo) of the paddock.  
2. **Label tiles** — in **CVAT** (or similar), mark weeds on map tiles for training data.  
3. **Train** a small **YOLOv8n-seg** model (segment weeds, not just boxes).  
4. **Generate waypoints** — turn detections into a short spot-spray path (wind/terrain-aware planning in the docs); write a QGroundControl mission file for review.  
5. **SITL test** — run the software against a simulated aircraft on the PC.  
6. **HIL test** — same ideas with the real flight controller on the bench.  
7. **Bench pump test** — water only; measure flow so dose timing matches the formula.  
8. **Field spray** — operator enables live ID; aircraft visits mapped weeds; **approach-scan** watches for extra weeds near each mapped target; pump pulses only when gates pass; pilot can inhibit anytime.

Between seasons: keep last year’s weed map, overlay new detections, and improve the model — a **yearly map flywheel**.

---

## 5. Key design decisions

| Decision | Plain meaning |
|----------|----------------|
| **Fail closed** | Doubt → pump off, no auto-spray. Bad video, bad position confidence, or lost RC does not leave chemical flowing. |
| **ELRS is hard authority** | The handheld radio always outranks ground software and any AI. |
| **LLM never controls flight** | The Ollama chat assistant may advise and call read-only or planning tools; it must not move sticks or bypass the safety gate. |
| **Only one spray writer** | `mavlink_hub` is the only code allowed to send the sprayer start/stop command. |
| **Dose formula** | Pulse volume from canopy size: \(V = \mathrm{clamp}(0.25 + 1.25 \times A_{\mathrm{cm}^2},\, 0.1,\, 2.5)\) millilitres; skip tiny speckles under **0.5 cm²**. Tank is **0.5 L** concentrate mix (mix fresh per label). |
| **Confidence gates** | Live detect needs model confidence **≥ 0.50** and position-on-ground confidence (**georeg**) **≥ 0.70**. |
| **Video ≠ control** | Camera radio is 5 GHz; pilot radio is 2.4 GHz — kept separate so one failure mode does not take both. |
| **Pi is not the brain** | Live spotting stays on the powerful ground PC; Pi only bridges. |
| **Yearly overlay flywheel** | Maps and labels compound; each season gets a better prior for where weeds return. |

---

## 6. Current status

| Layer | State |
|-------|--------|
| **Specced** | Airframe class, radios, camera, RTK pair, live pipeline numbers, voice rules, simulation ladder, gap list — largely written in `docs/`. |
| **Coded** | `drone_stack/` middleware: telemetry node, safety gate, dose-aware mission planner, mavlink hub, advisory Ollama agent, SITL launcher helpers; plus simulation stub scripts under `scripts/`. |
| **Tested** | Software syntax and offline smoke checks for dose/gate/planner; **not** yet a claimed full SITL→HIL→field chemical campaign in this repo. |
| **Next (from GAPS P0)** | (1) Power distribution / BEC rail map, (2) concrete failsafe parameters + Lua, (3) lock pump/nozzle/boom and a flow-calibration bench procedure. Then wire live YOLO into the hub and run the simulation ladder for real. |

Repo remains **local / private** until you choose to publish.

---

## 7. Risks and mitigations

| Risk | Why it matters | Mitigation in the design |
|------|----------------|---------------------------|
| **Thrust margin** | Wet weight ~2.7 kg on 8-inch props can be tight | Half-litre tank; thrust-stand gate before chemical; do not add mass without re-checking |
| **Pi 3B+ too slow for live ID** | Onboard neural net would miss the latency budget | Live YOLO stays on the ground station; Pi is bridge-only |
| **Georegistration under motion** | Wrong ground position → spray misses or hits pasture | Require georeg **≥ 0.70**; fail closed otherwise; RTK Fix preferred for spray |
| **Radio interference** | Video and RC on the same band fight each other | Video on **5 GHz**, ELRS on **2.4**; diversity RX notes; degrade video bitrate then freeze live spray if the link dies |
| **Unchosen pump/boom** | Dose timing needs real flow rate | Treat as P0 gap — water bench before chemical |
| **AI overconfidence** | A chat model inventing actions | Advisory-only agent; safety gate; single spray hub |

---

## Where to go deeper

| Topic | Doc |
|-------|-----|
| Mass, thrust, RTK, optional UWB | [AIRFRAME.md](AIRFRAME.md) |
| Live detect → dose → spray | [LIVE-PIPELINE.md](LIVE-PIPELINE.md) |
| Who may command what | [INTEGRATION.md](INTEGRATION.md) |
| Camera + video radio | [SYSTEM-SPEC.md](SYSTEM-SPEC.md) |
| What’s still missing | [GAPS.md](GAPS.md) |
| Middleware code | [../drone_stack/README.md](../drone_stack/README.md) |

---

*Compiled for presentation use from the precision-drone-spraying repository. Figures and prices only as already recorded in those docs; verify live vendor prices and local aviation/chemical rules before purchase or flight.*
