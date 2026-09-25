#!/usr/bin/env python3
"""Generate a synthetic ~25-weed / ~5-acre GeoJSON paddock for SITL/HIL.

Spec: docs/SIMULATION.md §1.3. Stub — sufficient for pipeline dry-runs.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

# ~5 acres ≈ 20234 m² → square side ≈ 142.25 m
METRES_PER_ACRE = 4046.8564224
METRES_PER_DEG_LAT = 111_320.0


def metres_per_deg_lon(lat_deg: float) -> float:
    return METRES_PER_DEG_LAT * max(0.2, math.cos(math.radians(lat_deg)))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--acres", type=float, default=5.0)
    p.add_argument("--count", type=int, default=25)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--origin-lat", type=float, default=-33.86)
    p.add_argument("--origin-lon", type=float, default=151.21)
    p.add_argument("--out", type=Path, default=Path("data/sim/weed_field.geojson"))
    p.add_argument("--cluster-frac", type=float, default=0.4,
                   help="Fraction of weeds placed near earlier seeds (patchy)")
    args = p.parse_args()

    rng = random.Random(args.seed)
    side_m = math.sqrt(args.acres * METRES_PER_ACRE)
    half = side_m / 2.0
    m_lon = metres_per_deg_lon(args.origin_lat)

    features = []
    seeds: list[tuple[float, float]] = []
    for i in range(args.count):
        if seeds and rng.random() < args.cluster_frac:
            sx, sy = rng.choice(seeds)
            # ~15–25 m cluster radius
            r = rng.uniform(5.0, 25.0)
            ang = rng.uniform(0, 2 * math.pi)
            x = sx + r * math.cos(ang)
            y = sy + r * math.sin(ang)
            x = max(-half, min(half, x))
            y = max(-half, min(half, y))
        else:
            x = rng.uniform(-half, half)
            y = rng.uniform(-half, half)
            seeds.append((x, y))

        lat = args.origin_lat + (y / METRES_PER_DEG_LAT)
        lon = args.origin_lon + (x / m_lon)
        canopy = round(rng.uniform(0.5, 4.0), 2)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "weed_id": i + 1,
                "class_id": rng.randint(0, 7),
                "canopy_cm2": canopy,
                "planted": True,
                "local_e_m": round(x, 3),
                "local_n_m": round(y, 3),
            },
        })

    fc = {
        "type": "FeatureCollection",
        "properties": {
            "seed": args.seed,
            "acres": args.acres,
            "side_m": round(side_m, 2),
            "origin_lat": args.origin_lat,
            "origin_lon": args.origin_lon,
            "count": args.count,
            "generator": "scripts/sim_weed_field.py",
        },
        "features": features,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fc, indent=2) + "\n")
    print(f"Wrote {args.count} weeds → {args.out}")


if __name__ == "__main__":
    main()
