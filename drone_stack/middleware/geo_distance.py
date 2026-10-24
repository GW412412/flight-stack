"""Distance between two lat/lon points, for planning code.

Contributed by Maya (AI agent, Sesame). Experimental.

The map-to-route planner used to import `safety_gate._dist_m`, a private
helper of the flight safety module. This public copy uses the same formula
(haversine on a sphere of radius 6 371 000 m), so planner and gate agree on
every distance, without the planner reaching into the gate. `safety_gate.py`
is deliberately left untouched: changing it counts as touching flight safety
and needs the owner's explicit OK (CONTRIBUTING.md).

A test (tests/test_map_route.py) checks that the two agree.
"""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6371000.0   # the same radius safety_gate and mav_node use


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance in metres between two points given
    in decimal degrees."""
    # Step 1: degrees to radians.
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    # Step 2: haversine of the central angle.
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    # Step 3: central angle times the radius.
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
