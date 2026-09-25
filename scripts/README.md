# Script notes

- `example-sprayer-rtl-off.lua` — illustrative RTL/Land shutoff using aux function 15.
- Official Lua docs: https://ardupilot.org/copter/docs/common-lua-scripts.html
- Prefer official applets under `libraries/AP_Scripting/applets` when available for your firmware branch.
- Never use blocking wait loops in Lua scripts.

## Live-stream frame extraction

[`extract_frames.py`](extract_frames.py) uses `ffmpeg` to sample a live RTSP or UDP stream and save numbered JPEGs. Install `ffmpeg` and run it with Python 3; the default output directory is `./frames` and the default interval is one frame per second (0.5–1.0 seconds is a useful starting range).

RTSP example:

```bash
python3 scripts/extract_frames.py \
  --input 'rtsp://USER:PASS@HOST/stream' \
  --output-dir ./frames --interval 1.0 --resolution 1920x1080 --jpeg-quality 2
```

UDP example:

```bash
python3 scripts/extract_frames.py \
  --input 'udp://@:5000' --output-dir ./frames --interval 0.5
```

The script runs until the stream ends; press **Ctrl-C** to stop cleanly. For a quick manual alternative:

```bash
ffmpeg -i "rtsp://USER:PASS@HOST/stream" -vf fps=1 -q:v 2 frames/frame_%06d.jpg
```

## Simulation stubs (SITL / HIL)

See [docs/SIMULATION.md](../docs/SIMULATION.md) for the normative spec.

- `sim_weed_field.py` — random ~25-weed / ~5-acre GeoJSON generator
- `spray_sim_logger.py` — MAVLink `DO_SPRAYER` / aux-15 JSONL logger (`--demo` offline)
- `plot_spray_coverage.py` — 2D coverage plot (optional `--three-d`)

```bash
python3 scripts/sim_weed_field.py --seed 42 --out data/sim/weed_field.geojson
python3 scripts/spray_sim_logger.py --demo --out logs/sim/spray_events.jsonl
python3 scripts/plot_spray_coverage.py \
  --weeds data/sim/weed_field.geojson \
  --events logs/sim/spray_events.jsonl \
  --out logs/sim/coverage_2d.png
```
