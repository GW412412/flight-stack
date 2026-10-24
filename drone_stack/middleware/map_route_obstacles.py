"""Legs between waypoints: obstacle clearance, no-fly zones and flying time.

Contributed by Maya (AI agent, Sesame). Experimental: synthetic tests only.
Split out of map_route.py after Grok's review of PR #5.

A "leg" is one straight flight from a = (x, y, alt) to b = (x, y, alt), in
map metres with alt relative to home. For every leg the checker answers:

  - Can we fly it at all? Not if it crosses a no-fly zone, passes over ground
    with no elevation data, or would need to climb above the gate's
    max_alt_m to clear something.
  - Is the straight 3-D line clear? It samples the surface model (DSM: the
    tops of trees and sheds; DTM if there is no DSM) along a corridor and
    needs obstacle_clearance_m above the tallest point.
  - If not, how high must we climb? Then the leg becomes climb in place to a
    "transit" height, fly across, descend in place.
  - How long does it take? horizontal / cruise speed + climb / climb rate +
    descent / descent rate. Climbing is slower than descending, so A->B and
    B->A can differ: the costs are asymmetric.

Nothing here talks to a vehicle. It only reads the map and does arithmetic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from shapely.geometry import LineString, Point

BIG = 1e9            # "infeasible" leg cost; finite so 2-opt arithmetic stays sane


@dataclass
class Leg:
    cost_s: float
    transit_alt_m: Optional[float]   # None = the straight leg is clear
    feasible: bool
    why: str = ""


def ceil_half_m(a: float) -> float:
    """Round a height UP to the next 0.5 m. Heights only ever round up."""
    return math.ceil(a * 2.0) / 2.0


class LegChecker:
    """Checks and prices legs over one ODM map.

    odm_map:      map_rasters.OdmMap (needs .dtm; .dsm optional)
    home_ground:  DTM height at home, so every height is "relative to home"
    home_xy:      home in map metres
    no_fly:       shapely polygons in map metres
    cfg:          map_route.RouteConfig (speeds, clearances, rtl_alt_m, ...)
    max_alt_m:    the gate's ceiling (SafetyConfig.max_alt_m)
    """

    def __init__(self, odm_map, home_ground: float, home_xy: Tuple[float, float],
                 no_fly, cfg, max_alt_m: float):
        self.m = odm_map
        self.home_ground = home_ground
        self.hx, self.hy = home_xy
        self.no_fly = list(no_fly or [])
        self.cfg = cfg
        self.max_alt_m = max_alt_m
        self._cache: Dict[tuple, Leg] = {}

    # ------------------------------------------------------------ surface
    def surface_rel(self, x: float, y: float, half: float) -> Optional[float]:
        """Tallest surface (DSM, else DTM) within `half` metres of (x, y),
        relative to home ground. None where the map has no data."""
        src = self.m.dsm or self.m.dtm
        z = src.max_in_box(x, y, half)
        return None if z is None else z - self.home_ground

    def crosses_no_fly(self, a, b) -> bool:
        """Does the straight line a -> b (map metres) touch any no-fly zone?"""
        if not self.no_fly:
            return False
        ln = LineString([a, b]) if a != b else Point(a)
        return any(z.intersects(ln) for z in self.no_fly)

    # ---------------------------------------------------------------- legs
    def leg(self, a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Leg:
        """Fly from a = (x, y, alt) to b. Results are cached."""
        key = (round(a[0], 3), round(a[1], 3), a[2], round(b[0], 3), round(b[1], 3), b[2])
        if key in self._cache:
            return self._cache[key]
        res = self._leg_uncached(a, b)
        self._cache[key] = res
        return res

    def _leg_uncached(self, a, b) -> Leg:
        c = self.cfg
        (ax, ay, aa), (bx, by, ba) = a, b
        d = math.hypot(bx - ax, by - ay)              # horizontal distance

        # Step 1: a leg through a no-fly zone is never flown. (The planner does
        # not route AROUND zones; it only refuses legs that cross them.)
        if self.crosses_no_fly((ax, ay), (bx, by)):
            return Leg(BIG, None, False, "crosses no-fly zone")

        # Step 2: walk along the leg every leg_sample_step_m. At each sample
        # find the tallest surface in a corridor either side, and the height
        # we would need to clear it.
        n = max(2, int(math.ceil(d / c.leg_sample_step_m)) + 1)
        need = -1e9          # highest clearance height needed anywhere on the leg
        clear = True         # does the straight a -> b line keep that clearance?
        for i in range(n):
            f = i / (n - 1)                            # 0 at a, 1 at b
            px, py = ax + f * (bx - ax), ay + f * (by - ay)
            surf = self.surface_rel(px, py, c.leg_corridor_half_width_m)
            if surf is None:
                # Hole in the map: we cannot know what is there, so refuse.
                return Leg(BIG, None, False, "leg crosses ground with no elevation data")
            req = surf + c.obstacle_clearance_m
            need = max(need, req)
            # Height of the straight line at this sample, linearly between
            # a's and b's altitudes.
            if aa + f * (ba - aa) < req - 1e-6:
                clear = False

        # Step 3a: clear -> fly it straight. Time = horizontal at cruise, plus
        # any climb at the climb rate, or any descent at the descent rate.
        if clear:
            cost = d / c.cruise_ms + max(0.0, ba - aa) / c.climb_ms \
                + max(0.0, aa - ba) / c.descend_ms
            return Leg(cost, None, True)

        # Step 3b: not clear -> climb in place to a transit height above the
        # tallest obstacle, cross, then descend in place at b.
        t_alt = ceil_half_m(max(need, aa, ba))
        if t_alt > self.max_alt_m:
            # Never plan above the gate's ceiling.
            return Leg(BIG, t_alt, False, f"needs transit {t_alt:.1f} m > max_alt_m")
        cost = (t_alt - aa) / c.climb_ms + d / c.cruise_ms + (t_alt - ba) / c.descend_ms
        return Leg(cost, t_alt, True)

    def return_leg(self, x: float, y: float, alt_m: float) -> Leg:
        """RTL from a target at (x, y, alt_m).

        ArduPilot's RTL climbs to RTL_ALT if it is lower, flies straight home
        at that height, then lands. We check that straight line against the
        DSM. If it is not clear, the leg carries a transit height: the planner
        adds a climb-in-place waypoint above the last target, so RTL starts
        high enough (RTL keeps the current height when it is already above
        RTL_ALT)."""
        c = self.cfg
        # Step 1: the height RTL will fly home at.
        start = max(alt_m, c.rtl_alt_m)
        # Step 2: is the flat line home at that height clear?
        flat = self.leg((x, y, start), (self.hx, self.hy, start))
        if not flat.feasible:
            return flat
        # Step 3: time = climb to the cruise height, fly home, descend to land.
        top = flat.transit_alt_m or start
        d = math.hypot(x - self.hx, y - self.hy)
        cost = (top - alt_m) / c.climb_ms + d / c.cruise_ms + top / c.descend_ms
        # transit_alt_m is set only when RTL's own climb to rtl_alt_m is not
        # enough and a climb-in-place waypoint must be added before RTL.
        return Leg(cost, flat.transit_alt_m, True)
