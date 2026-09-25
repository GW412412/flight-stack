#!/usr/bin/env python3
"""2D (required) coverage plot: weed truth vs spray hits. Optional --3d stub.

Spec: docs/SIMULATION.md §3.2. Hit = pulse within 1.0 m of a weed feature.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

METRES_PER_DEG_LAT = 111_320.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def load_weeds(path: Path) -> list[dict]:
    fc = json.loads(path.read_text())
    out = []
    for f in fc.get("features", []):
        lon, lat = f["geometry"]["coordinates"][:2]
        props = f.get("properties", {})
        out.append({"lat": lat, "lon": lon, "weed_id": props.get("weed_id"), "hit": False})
    return out


def load_pulses(path: Path) -> list[dict]:
    pulses = []
    if not path.exists():
        return pulses
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        # Count enable edges (param1 == 1) or demo enables
        if ev.get("cmd") == "MAV_CMD_DO_SPRAYER" and ev.get("param1") == 1:
            if ev.get("lat") is not None and ev.get("lon") is not None:
                pulses.append(ev)
        elif ev.get("cmd") == "MAV_CMD_DO_AUX_FUNCTION" and ev.get("param2", 0) >= 1:
            if ev.get("lat") is not None and ev.get("lon") is not None:
                pulses.append(ev)
    return pulses


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--weeds", type=Path, default=Path("data/sim/weed_field.geojson"))
    p.add_argument("--events", type=Path, default=Path("logs/sim/spray_events.jsonl"))
    p.add_argument("--out", type=Path, default=Path("logs/sim/coverage_2d.png"))
    p.add_argument("--hit-radius-m", type=float, default=1.0)
    p.add_argument("--three-d", action="store_true", dest="three_d",
                   help="Optional 3D scatter (needs matplotlib)")
    args = p.parse_args()

    weeds = load_weeds(args.weeds)
    pulses = load_pulses(args.events)

    hits = 0
    false_sprays = 0
    for pul in pulses:
        best_i, best_d = None, 1e9
        for i, w in enumerate(weeds):
            d = haversine_m(pul["lat"], pul["lon"], w["lat"], w["lon"])
            if d < best_d:
                best_d, best_i = d, i
        if best_i is not None and best_d <= args.hit_radius_m:
            if not weeds[best_i]["hit"]:
                hits += 1
            weeds[best_i]["hit"] = True
            pul["matched_weed"] = weeds[best_i]["weed_id"]
        else:
            false_sprays += 1
            pul["matched_weed"] = None

    misses = sum(1 for w in weeds if not w["hit"])
    print(f"weeds={len(weeds)} pulses={len(pulses)} "
          f"unique_hits={hits} misses={misses} false_sprays={false_sprays}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        summary = args.out.with_suffix(".txt")
        summary.write_text(
            f"matplotlib missing — metrics only\n"
            f"weeds={len(weeds)} pulses={len(pulses)} "
            f"unique_hits={hits} misses={misses} false_sprays={false_sprays}\n"
        )
        print(f"Wrote metrics → {summary}")
        return

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_title("Spray coverage (sim)")
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    ax.scatter([w["lon"] for w in weeds if not w["hit"]],
               [w["lat"] for w in weeds if not w["hit"]],
               c="red", marker="x", label="miss", zorder=3)
    ax.scatter([w["lon"] for w in weeds if w["hit"]],
               [w["lat"] for w in weeds if w["hit"]],
               c="green", marker="o", label="hit weed", zorder=3)
    ax.scatter([p["lon"] for p in pulses],
               [p["lat"] for p in pulses],
               c="blue", marker="+", label="spray pulse", zorder=2)
    ax.legend(loc="best")
    ax.set_aspect("equal", adjustable="datalim")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=120)
    plt.close(fig)
    print(f"Wrote {args.out}")

    if args.three_d:
        # Optional stub: flat AGL plane
        fig = plt.figure(figsize=(8, 8))
        ax3 = fig.add_subplot(111, projection="3d")
        ax3.scatter([w["lon"] for w in weeds], [w["lat"] for w in weeds],
                    [0] * len(weeds), c="green", label="weeds")
        ax3.scatter([p["lon"] for p in pulses], [p["lat"] for p in pulses],
                    [p.get("alt_m") or 10 for p in pulses], c="blue", label="pulses")
        ax3.set_xlabel("lon")
        ax3.set_ylabel("lat")
        ax3.set_zlabel("alt_m")
        out3 = args.out.with_name("coverage_3d.png")
        fig.savefig(out3, dpi=120)
        plt.close(fig)
        print(f"Wrote {out3}")


if __name__ == "__main__":
    main()
