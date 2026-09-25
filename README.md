# Precision Drone Spraying — ArduPilot Stack Research

Local research notes and starter configs for building a **precision agricultural spray drone** on the **ArduPilot / ArduCopter** software stack.

> **Status:** Local git repo only. Not pushed. Not made public.  
> Content is research notes + clearly marked **example** configs — not official vendor recommendations.

## What’s in this repo

| Path | Purpose |
|------|---------|
| [docs/STACK.md](docs/STACK.md) | Full-stack summary: firmware → spray extensions → companion PC → GCS → base station |
| [docs/SETUP-NOTES.md](docs/SETUP-NOTES.md) | Practical setup checklist and ordering of work |
| [config/](config/) | Example parameter snippets (marked as examples) |
| [scripts/](scripts/) | Placeholder / example Lua sketch notes for spray helpers |

## How to use

1. Read **docs/STACK.md** for the map of official repos, docs, and community projects.
2. Walk **docs/SETUP-NOTES.md** before flashing or wiring pumps.
3. Copy **config/** snippets into your GCS only after adapting SERVO channel numbers, PWM ranges, and rates to *your* pump and airframe.
4. Keep this repo private until you explicitly choose to publish.

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
