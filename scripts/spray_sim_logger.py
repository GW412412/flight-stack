#!/usr/bin/env python3
"""Log MAV_CMD_DO_SPRAYER / aux-15 events for SITL/HIL virtual sprayer review.

Spec: docs/SIMULATION.md §3.1. Stub — requires pymavlink; no FC required for --demo.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# MAVLink command ids (ArduPilot / common)
MAV_CMD_DO_SPRAYER = 216
MAV_CMD_DO_AUX_FUNCTION = 218
AUX_SPRAYER = 15


def append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def demo_run(out: Path) -> None:
    """Synthetic pulses so plot_spray_coverage.py can be exercised offline."""
    t0 = time.time()
    for i, (lat, lon, dose, wid) in enumerate([
        (-33.8601, 151.2102, 1.2, 1),
        (-33.8603, 151.2105, 0.8, 2),
        (-33.8598, 151.2098, 2.1, 3),
    ]):
        append_event(out, {
            "t_utc": datetime.now(timezone.utc).isoformat(),
            "t_mono": t0 + i * 2.0,
            "cmd": "MAV_CMD_DO_SPRAYER",
            "param1": 1,
            "lat": lat,
            "lon": lon,
            "alt_m": 10.0,
            "gs_m_s": 3.0,
            "yaw_deg": 90.0,
            "dose_mL": dose,
            "weed_id": wid,
            "section": 1,
            "pwm_pump": None,
            "source": "demo",
        })
        append_event(out, {
            "t_utc": datetime.now(timezone.utc).isoformat(),
            "t_mono": t0 + i * 2.0 + 0.15,
            "cmd": "MAV_CMD_DO_SPRAYER",
            "param1": 0,
            "lat": lat,
            "lon": lon,
            "alt_m": 10.0,
            "gs_m_s": 3.0,
            "yaw_deg": 90.0,
            "dose_mL": None,
            "weed_id": wid,
            "section": 1,
            "pwm_pump": None,
            "source": "demo",
        })
    print(f"Demo wrote pulses → {out}")


def listen(connection: str, out: Path) -> None:
    try:
        from pymavlink import mavutil
    except ImportError as e:
        raise SystemExit(
            "pymavlink required for live listen (pip install pymavlink), "
            "or pass --demo"
        ) from e

    m = mavutil.mavlink_connection(connection)
    m.wait_heartbeat()
    print(f"Heartbeat from system {m.target_system}; logging to {out}")

    pos = {"lat": None, "lon": None, "alt_m": None, "gs_m_s": None, "yaw_deg": None}

    while True:
        msg = m.recv_match(blocking=True, timeout=1.0)
        if msg is None:
            continue
        t = msg.get_type()
        if t == "GLOBAL_POSITION_INT":
            pos["lat"] = msg.lat / 1e7
            pos["lon"] = msg.lon / 1e7
            pos["alt_m"] = msg.relative_alt / 1000.0
            pos["gs_m_s"] = (msg.vx**2 + msg.vy**2) ** 0.5 / 100.0
        elif t == "ATTITUDE":
            pos["yaw_deg"] = msg.yaw * 180.0 / 3.141592653589793
        elif t == "COMMAND_LONG":
            if msg.command == MAV_CMD_DO_SPRAYER:
                append_event(out, {
                    "t_utc": datetime.now(timezone.utc).isoformat(),
                    "t_mono": time.monotonic(),
                    "cmd": "MAV_CMD_DO_SPRAYER",
                    "param1": msg.param1,
                    **pos,
                    "dose_mL": None,
                    "weed_id": None,
                    "section": None,
                    "pwm_pump": None,
                    "source": connection,
                })
            elif msg.command == MAV_CMD_DO_AUX_FUNCTION and int(msg.param1) == AUX_SPRAYER:
                append_event(out, {
                    "t_utc": datetime.now(timezone.utc).isoformat(),
                    "t_mono": time.monotonic(),
                    "cmd": "MAV_CMD_DO_AUX_FUNCTION",
                    "param1": msg.param1,
                    "param2": msg.param2,
                    **pos,
                    "dose_mL": None,
                    "weed_id": None,
                    "section": None,
                    "pwm_pump": None,
                    "source": connection,
                })


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--connection", default="udp:127.0.0.1:14550",
                   help="pymavlink connection string")
    p.add_argument("--out", type=Path, default=Path("logs/sim/spray_events.jsonl"))
    p.add_argument("--demo", action="store_true",
                   help="Write synthetic events (no MAVLink)")
    args = p.parse_args()
    if args.demo:
        demo_run(args.out)
    else:
        listen(args.connection, args.out)


if __name__ == "__main__":
    main()
