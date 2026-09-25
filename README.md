# Precision Drone Spraying — ArduPilot Stack Research

Local research notes and starter configs for building a **precision agricultural spray drone** on the **ArduPilot / ArduCopter** software stack.

> **Status:** Local git repo only. Not pushed. Not made public.  
> Content is research notes + clearly marked **example** configs — not official vendor recommendations.

## What’s in this repo

| Path | Purpose |
|------|---------|
| [docs/STACK.md](docs/STACK.md) | Full-stack summary: firmware → spray extensions → companion PC → GCS → base station |
| [docs/SETUP-NOTES.md](docs/SETUP-NOTES.md) | Practical setup checklist and ordering of work |
| [docs/WEED-ID.md](docs/WEED-ID.md) | Weed ID from ODM orthomosaics (models, datasets, K80/CPU inference) |
| [docs/WAYPOINT-OPT.md](docs/WAYPOINT-OPT.md) | Economic spot-spray waypoint optimisation (TSP/ATSP, wind, terrain, 45° spray) |
| [docs/OPENIPC.md](docs/OPENIPC.md) | OpenIPC live video / Wi‑Fi camera research (RTSP, FPV, ELRS coexistence) |
| [docs/OPENIPC-CAMERAS.md](docs/OPENIPC-CAMERAS.md) | Lightweight OpenIPC camera / AIO modules for spot-spray (sensors, weight, RTSP) |
| System spec (camera + radio) | [docs/SYSTEM-SPEC.md](docs/SYSTEM-SPEC.md) |
| [docs/OBSTACLE-AVOIDANCE.md](docs/OBSTACLE-AVOIDANCE.md) | Collision/obstacle avoidance for low-altitude Guided spray (sensors, OA_TYPE, FC-only kit) |
| [config/](config/) | Example parameter snippets (marked as examples) |
| [scripts/](scripts/) | Example Lua sketch and live-stream frame extraction |

## How to use

1. Read **docs/STACK.md** for the map of official repos, docs, and community projects.
2. Walk **docs/SETUP-NOTES.md** before flashing or wiring pumps.
3. See **docs/WEED-ID.md**, **docs/WAYPOINT-OPT.md**, **docs/OPENIPC.md**, **docs/OPENIPC-CAMERAS.md**, and **docs/OBSTACLE-AVOIDANCE.md** for perception, planning, camera-link, and OA research notes.
| System spec (camera + radio) | [docs/SYSTEM-SPEC.md](docs/SYSTEM-SPEC.md) |
4. Copy **config/** snippets into your GCS only after adapting SERVO channel numbers, PWM ranges, and rates to *your* pump and airframe.
5. Keep this repo private until you explicitly choose to publish.

## Stack at a glance

```
┌─────────────────────────────────────────────────────────┐
│  GCS / Base station                                      │
│  Mission Planner · QGroundControl · field planners · RTK │
└───────────────────────────┬─────────────────────────────┘
                            │ MAVLink / RTCM
┌───────────────────────────▼─────────────────────────────┐
│  Companion computer (optional)                           │
│  RPi / Jetson · BlueOS / Rpanion · MAVROS / ROS 2 / SDK  │
└───────────────────────────┬─────────────────────────────┘
                            │ UART / Ethernet MAVLink
┌───────────────────────────▼─────────────────────────────┐
│  Flight controller — ArduCopter + AC_Sprayer             │
│  SPRAY_* params · SERVO 22/23 · RC option 15 · Lua       │
└───────────────────────────┬─────────────────────────────┘
                            │ PWM / relay
┌───────────────────────────▼─────────────────────────────┐
│  Pump + optional spinner / rotary atomizer               │
└─────────────────────────────────────────────────────────┘
```

## Important caveats

- **AC_Sprayer** is documented for **Copter**; many **1 MB flash** boards omit it (see Firmware Limitations).
- Pump rate scales with **ground speed** — you must calibrate `SPRAY_PUMP_RATE` for your nozzle/pump, not copy defaults blindly.
- Tank-empty / flow-meter shutoff is **not** first-class in AC_Sprayer; use **Lua scripting** or companion logic.
- **APSync** is listed as **deprecated**; prefer BlueOS or Rpanion-server for companion images.

## License

MIT for these research notes (see [LICENSE](LICENSE)). Upstream projects keep their own licenses.

- [Labeling workflow](docs/LABELING-WORKFLOW.md)
