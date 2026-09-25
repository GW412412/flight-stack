# System integration — modules, buses, and data flow

Single glue spec for how the precision spot-spraying pieces connect in
**v1**. Individual behaviours stay in their own docs; this file is the
**wiring diagram** and authority order. Compiled 2026-09-26.

**Ops shape:** touch-up on otherwise clean paddocks (~25 weeds / ~5 acres).
See [LIVE-PIPELINE.md](LIVE-PIPELINE.md) operational context.

**Airframe (locked):** F450-class + 8×3 props; **32-bit Pixhawk-class** FC; single
**Raspberry Pi 3B+** companion (bridge/logs only). Details: [AIRFRAME.md](AIRFRAME.md).
Live YOLOv8n-seg spray decisions stay on the **GS**, not the Pi.

---

## 1. Authority order (never invert)

| Rank | Layer | May do | Must not |
|------|-------|--------|----------|
| 1 | **Pilot + ELRS (RC)** | Fly, mode switch, arm, kill, sprayer inhibit | — |
| 2 | **Flight controller** (**Pixhawk 4 / Cube Orange-class**, ArduCopter + OA + `AC_Sprayer`) | Modes, failsafes, Simple Avoidance / BendyRuler, pump PWM | — |
| 3 | **GS MAVLink services** (live ID, voice, mission upload) | Suggest modes, pulse sprayer, upload waypoints | Override RC; carry RC on video RF |
| 4 | **Operator GCS UI** | Plan, monitor, abort | Bypass ELRS for flight |

If layers disagree: **lower rank yields**. Voice and live ID are convenience
only. Video RF (WFB-NG) carries **pixels only** — never RC, never spray
commands.

---

## 2. Module map (what owns what)

| Module | Spec | Runs on | Talks via |
|--------|------|---------|-----------|
| Camera + video RF | [SYSTEM-SPEC.md](SYSTEM-SPEC.md), [OPENIPC.md](OPENIPC.md), [OPENIPC-CAMERAS.md](OPENIPC-CAMERAS.md) | Air Thinker/IMX335 → GS decode | WFB-NG 5 GHz → local RTP/RTSP |
| Offline weed map | [WEED-ID.md](WEED-ID.md), [LABELING-WORKFLOW.md](LABELING-WORKFLOW.md) | GS (CPU/ONNX) | GeoTIFF → GeoJSON/CSV |
| Live ID → nozzle | [LIVE-PIPELINE.md](LIVE-PIPELINE.md) | GS | RTSP frames + telem → `MAV_CMD_DO_SPRAYER` |
| Waypoint / cluster mission | [WAYPOINT-OPT.md](WAYPOINT-OPT.md) | GS (offline) | GeoJSON → `.waypoints` / QGC WPL → FC |
| Voice modes | [VOICE-CONTROL.md](VOICE-CONTROL.md) | GS | Mic → STT → read-back → `MAV_CMD_DO_SET_MODE` |
| Sprayer hardware | [STACK.md](STACK.md) | FC | `AC_Sprayer` / relays / Lua |
| Obstacle / terrain | [OBSTACLE-AVOIDANCE.md](OBSTACLE-AVOIDANCE.md) | **FC only** (v1) | Rangefinders → PRX / OA |
| Stack / GCS / RTK | [STACK.md](STACK.md), [SETUP-NOTES.md](SETUP-NOTES.md) | GS + base | MAVLink + RTCM |
| Airframe / companion | [AIRFRAME.md](AIRFRAME.md) | **Pixhawk-class FC** + **Pi 3B+** (only companions) | ELRS RC; UART MAVLink; video bypasses Pi |
| OpenRTLS (optional UWB) | [AIRFRAME.md](AIRFRAME.md) §6.1 | GS solver + air tag; anchors at zone edge | UWB ranges → pose → Pi/telem `VISION_POSITION_ESTIMATE` (not WFB) |

---

## 3. End-to-end data-flow diagram

```
                         ┌──────────────────────────────────────────┐
                         │            GROUND STATION (GS)            │
                         │                                           │
  headset mic ──PTT/wake─► Voice service                             │
                         │   STT → whitelist → read-back → confirm   │
                         │   → MAV_CMD_DO_SET_MODE ───────┐          │
                         │   logs/voice-commands.jsonl    │          │
                         │                                │          │
  RTL8812AU ◄──WFB-NG──┐ │                                │          │
  (5 GHz video only)   │ │                                ▼          │
                       ├─► Video decode → RTP/RTSP :5600              │
                       │ │         │                      │          │
                       │ │         ▼                      │          │
                       │ │   Live ID service              │          │
                       │ │   ROI→640→YOLOv8n-seg ONNX     │          │
                       │ │   georeg → dose → section      │          │
                       │ │   → MAV_CMD_DO_SPRAYER ────────┤          │
                       │ │   logs/live-pipeline.jsonl     │          │
                       │ │         │                      │          │
                       │ │         │ (shadow / map export)│          │
                       │ │         ▼                      │          │
                       │ │   Offline map path (batch)     │          │
                       │ │   ODM ortho → WEED-ID tiles    │          │
                       │ │   → GeoJSON weeds              │          │
                       │ │         │                      │          │
                       │ │         ▼                      │          │
                       │ │   Waypoint optimiser           │          │
                       │ │   cluster → ATSP + wind/DEM    │          │
                       │ │   +45° departure → mission     │          │
                       │ │         │                      │          │
                       │ │         ▼                      ▼          │
                       │ │   MAVLink router / MAVProxy / pymavlink   │
                       │ │         │                                 │
                       └───────────┼─────────────────────────────────┘
                                   │ telemetry (SiK / ELRS telem /
                                   │ USB / Ethernet) — NOT video RF
                                   ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ AIRFRAME — F450 + 8×3 props ([AIRFRAME.md](AIRFRAME.md))         │
 │                                                                  │
 │  ELRS RX (2.4) ◄════ RC sticks / modes / arm / sprayer inhibit   │
 │       │                                                          │
 │       ▼                                                          │
 │  Pixhawk 4 / Cube Orange-class (ArduCopter + AC_Sprayer)         │
 │    ├─ flight modes, failsafes                                    │
 │    ├─ OA: TFmini(+)/SF45 → Simple Avoidance + BendyRuler         │
 │    ├─ AC_Sprayer / section relays ◄── DO_SPRAYER / aux 15        │
 │    ├─ mission items (clustered touch-up waypoints)               │
 │    └─ UART/USB ──► Raspberry Pi 3B+ (mavlink bridge + logs ONLY; │
 │                    not live YOLO spray authority)                │
 │                                                                  │
 │  Thinker+IMX335 ──H.264 720p30──► RTL8812AU ──WFB-NG 5 GHz──► GS │
 │  (pixels only — does not transit the Pi)                         │
 │                                                                  │
 │  RTK GPS ◄── separate radio / NTRIP (not on video Wi‑Fi)         │
 │                                                                  │
 │  OpenRTLS tag (opt.) ──UART──► Pi → VISION_POSITION_ESTIMATE     │
 │  ←── UWB ── 4× DWM1000 anchors ──Ethernet──► GS solver           │
 │  (fuse with RTK near trees; not on video Wi‑Fi)                  │
 └─────────────────────────────────────────────────────────────────┘
```

---

## 4. Shared buses (v1 locks)

| Bus | Medium | Payload | Forbidden |
|-----|--------|---------|-----------|
| **RC** | ELRS 2.4 GHz | Sticks, mode, arm, sprayer switch | Video, weed ID |
| **Video** | WFB-NG 5 GHz | H.264 (and GS restream) | RC, MAVLink spray, RTK |
| **Telem** | Serial / ELRS telem / SiK / Ethernet (optional via **Pi 3B+** router) | MAVLink (modes, spray, mission, GPS, attitude) | High-rate video; live ONNX on Pi |
| **RTK** | Dedicated / NTRIP | RTCM | Sharing video Wi‑Fi |
| **OpenRTLS (optional)** | UWB (DWM1000) + Ethernet/UART | Local XYZ pose → MAVLink external nav | Video RF; replacing RTK in open sky |
| **GS IPC** | localhost RTP + Unix sockets / shared logs | Frames, JSON events | Crossing to air except via telem |

One **MAVLink endpoint** on the GS (MAVProxy or a small pymavlink hub) fans
out to voice, live ID, mission tools, and the operator GCS. Do not open
parallel competing spray writers without a mutex / single “spray arbiter”
process.

---

## 4.1 OpenRTLS (optional) — UWB aid beside RTK

**Optional** precision layer using open-source **OpenRTLS / Open-RTLS** UWB stacks
(DWM1000 anchors + tag). Complements Holybro F9P RTK when the rover loses Fix
near trees or structures. Details and cost: [AIRFRAME.md](AIRFRAME.md) §6.1.

| Link | Medium | Role |
|------|--------|------|
| ELRS | 2.4 GHz | RC (unchanged authority) |
| WFB-NG | 5 GHz | Video pixels only |
| SiK / ELRS telem | Sub‑GHz / 2.4 telem | MAVLink + RTCM inject |
| RTK | F9P base → GCS → telem | Primary outdoor cm GPS |
| **OpenRTLS** | UWB + Ethernet/UART | Local cm-class pose in GNSS-denied pockets |

**Feed path:** 4+ anchors surveyed around the problem zone → GS OpenRTLS solver
→ air tag UART into **Pi 3B+** (or pose over telem) → MAVLink
`VISION_POSITION_ESTIMATE` (or `GPS_INPUT`) → EKF3 alongside RTK.
Minimal AliExpress DWM1000 kit: **4 anchors + 1 tag ≈ $100–200**.

**Follow-up slot:** caller indicated **“and also …”** — another radios /
precision item may land next; keep this subsection open for that addition
without renumbering the whole integration map.

---

## 5. Operating modes (how modules combine)

| Mode | Video | Live ID | Mission | Voice | Pump |
|------|-------|---------|---------|-------|------|
| **A. Scout / map** | Record stills or low-rate extract | Off or shadow | Upload survey grid only | Optional Loiter | Off |
| **B. Live touch-up** | 720p30 ID stream | **On** (conf ≥ 0.50) | Optional loiter path | On (hold/RTL) | Pulses from live ID |
| **C. Clustered revisit** | Optional monitor | Shadow or off | **ATSP clusters** from GeoJSON | On | Mission `DO_SPRAYER` / live assist |
| **D. Degraded link** | Bitrate steps then stills | Freeze / off | Precomputed only | On | Fail closed off if video lost in B |
| **E. Abort** | Don’t care | Off | Clear / RTL | “RTL” / RC | Off |

Default field sequence for a clean paddock: **A → (optional B) → C** for
escapes, with RC able to force **E** at any time.

---

## 6. GS process sketch (modular services)

Suggested split (separate processes, one repo):

```
gs/
  mavlink_hub.py          # sole COMMAND_LONG writer + telem fan-out
  video_ingest.py         # WFB decode → RTP :5600
  live_id_service.py      # LIVE-PIPELINE
  voice_service.py        # VOICE-CONTROL
  mission_builder.py      # WEED-ID outputs → WAYPOINT-OPT → upload
  logs/                   # voice-commands.jsonl, live-pipeline.jsonl
```

**Spray arbiter rule:** only `mavlink_hub` sends `MAV_CMD_DO_SPRAYER`. Live ID
and mission_builder publish `SprayRequest` events; hub enforces cooldown,
RC inhibit, failsafe, and link-health flags from SYSTEM-SPEC / LIVE-PIPELINE.

**Voice rule:** voice may change **modes** (Loiter, RTL, …) via the hub; it
does **not** enable the pump directly (avoids accidental spray from “yes”).

---

## 7. Cross-cutting fail-closed rules

1. RC sprayer switch **off** → hub drops all `SprayRequest`s.  
2. RC failsafe / radio loss → voice and live ID **refuse** new commands.  
3. Video RF lost in live touch-up → **pump off**; continue on ELRS.  
4. Georeg confidence &lt; 0.70 → no live auto-spray (log only).  
5. OA / fence events are **FC-side**; GS must not fight BendyRuler with
   aggressive Guided setpoints (slow down or Loiter on voice/RC).  
6. No module may open a second path to motor kill — hardware/RC only.
7. OpenRTLS / UWB pose is an **aid only** — never a substitute for ELRS; if UWB drops, fall back to RTK/GPS and slow or Loiter near canopy.

---

## 8. Logs and post-flight bundle

| File | Source |
|------|--------|
| `logs/voice-commands.jsonl` | Voice service |
| `logs/live-pipeline.jsonl` | Live ID |
| `logs/mavlink-commands.jsonl` | Hub (modes + spray + mission uploads) |
| `data/maps/<flight>/weeds.geojson` | Offline WEED-ID |
| `data/maps/<flight>/mission.waypoints` | Waypoint optimiser |

Bundle these with FC `.bin` for any spray sortie that used auto pulses.

---

## 9. Implementation order (unblocks build)

1. SITL + `mavlink_hub` + sprayer params ([STACK.md](STACK.md), [SETUP-NOTES.md](SETUP-NOTES.md)).  
2. Voice whitelist on SITL ([VOICE-CONTROL.md](VOICE-CONTROL.md)).  
3. Video ingest bench (SYSTEM-SPEC) + `extract_frames.py`.  
4. Offline label → ONNX ([LABELING-WORKFLOW.md](LABELING-WORKFLOW.md)) to mAP/recall gates.  
5. Live ID shadow mode, then gated spray ([LIVE-PIPELINE.md](LIVE-PIPELINE.md)).  
6. Clustered mission builder ([WAYPOINT-OPT.md](WAYPOINT-OPT.md)).  
7. FC OA kit on vehicle ([OBSTACLE-AVOIDANCE.md](OBSTACLE-AVOIDANCE.md)).  
8. Field: scout map → touch-up → revisit; never skip SITL gates.

---

## 10. One-paragraph summary

ELRS flies the **F450** airframe; a **Pixhawk-class** FC owns avoidance and the
pump; a single **Pi 3B+** only bridges/logs MAVLink (not live YOLO); the
ground station **decodes video**, runs **voice** and **live YOLOv8n-seg**,
builds **clustered ATSP** missions, and sends **mode / spray MAVLink on
telemetry** through a single hub — WFB-NG never carries control — with
fail-closed rules so touch-up spray stays a precision convenience layer.
Optional **OpenRTLS** UWB can fuse with RTK near canopy without riding video RF.

---

## Related docs

- [AIRFRAME.md](AIRFRAME.md) · [SYSTEM-SPEC.md](SYSTEM-SPEC.md) · [LIVE-PIPELINE.md](LIVE-PIPELINE.md) ·
  [VOICE-CONTROL.md](VOICE-CONTROL.md) · [WEED-ID.md](WEED-ID.md) ·
  [WAYPOINT-OPT.md](WAYPOINT-OPT.md) · [STACK.md](STACK.md) ·
  [OBSTACLE-AVOIDANCE.md](OBSTACLE-AVOIDANCE.md) · [OPENIPC.md](OPENIPC.md)
