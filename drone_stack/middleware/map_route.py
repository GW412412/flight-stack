"""Georeferenced targets -> ordered, terrain-aware waypoint sorties.

Contributed by Maya (AI agent, Sesame). Experimental: synthetic tests only.

What it produces
----------------
For each sortie (one battery / one tank load) a list of plain nav waypoints
`[{"lat", "lon", "alt_m"}, ...]`, which is exactly what the existing
`upload_mission` tool takes (alt_m relative to HOME, the same frame the
safety gate checks and hub.upload_mission sends as GLOBAL_RELATIVE_ALT).
Alongside it, per-waypoint metadata for the hand-off to the visual centering
loop. There are NO sprayer commands and nothing here talks to a vehicle.

How
---
 1. Each target gets an approach altitude:
        alt = (DTM at target - DTM at home) + approach_agl_m
    raised if needed to clear the tallest thing (DSM) within
    target_clearance_radius_m by obstacle_clearance_m. Heights are
    differences inside one ODM map, so the unknown vertical datum cancels.
    Targets whose approach altitude would break the gate's
    [min_goto_alt_m, max_alt_m] are EXCLUDED with a reason, never clamped
    down towards the ground. (Too low -> raised to min_goto_alt_m if the
    camera height stays within max_approach_agl_m.)
 2. Filters, each with a reason in `excluded`: confidence < 0.50,
    area below the dose threshold (spray_mission.dose_ml -> None), outside the
    spray zone, inside a no-fly zone, outside geofence minus a margin,
    canopy taller than max_canopy_h_m.
 3. Every leg is checked against the DSM along a corridor. If the straight
    3-D line would pass within obstacle_clearance_m of a tree or shed, the
    leg gets two "transit" waypoints: climb in place to a safe height, fly
    across, descend in place. Legs through a no-fly polygon, over ground with
    no elevation data, or needing a transit height above max_alt_m are
    infeasible.
 4. Cost of a leg = time: horizontal / cruise + climb / climb rate +
    descent / descent rate (+ dwell at each target for centering). It is
    asymmetric on hills, which is why there is no off-the-shelf symmetric
    TSP call here.
 5. Order: nearest neighbour from home, then 2-opt on the asymmetric cost
    (route first). Then split into sorties greedily along that order so each
    sortie fits the tank (spray_mission.dose_ml), the battery time budget
    (with a reserve, return-to-home included) and max_mission_items
    (cluster second). Each sortie is then 2-opt'd again on its own.
 6. Each sortie is pre-checked with the real FlightGate.check_mission using
    the same SafetyConfig, so a plan the gate would refuse is caught at
    planning time. The gate still checks again at upload; nothing here
    replaces it.

What it does NOT do (see docs/MAP-TO-ROUTE.md "Gaps"): route AROUND no-fly
zones (it only refuses legs that cross them), model wind, know the real
battery, or know whether the map is still current.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from shapely.geometry import LineString, Point
import numpy as np
import shapely

from map_georef import Target
from map_rasters import MapError
from safety_gate import FlightGate
from spray_mission import CONF_MIN, TANK_LITRES_DEFAULT, dose_ml

BIG = 1e9            # "infeasible" leg cost; finite so 2-opt arithmetic stays sane


@dataclass
class RouteConfig:
    """Planner knobs. Every number marked PLACEHOLDER needs a real value from
    the airframe, camera or a bench test before it means anything.

    Hard limits (max_alt_m, min_goto_alt_m, geofence_radius_m, max_speed_ms,
    max_mission_items) are NOT here: they come from the stack's SafetyConfig
    so there is one source of truth."""
    approach_agl_m: float = 6.0          # PLACEHOLDER camera height over target for centering
    max_approach_agl_m: float = 12.0     # PLACEHOLDER above this the live camera can't resolve a rosette
    obstacle_clearance_m: float = 3.0    # min gap above the DSM (trees, sheds, fences)
    target_clearance_radius_m: float = 2.0   # look this far around a target for obstacles
    leg_corridor_half_width_m: float = 1.5   # look this far either side of a leg
    leg_sample_step_m: float = 0.5
    takeoff_alt_m: float = 8.0           # where a sortie starts (above home)
    rtl_alt_m: float = 15.0              # must match RTL_ALT_M on the Pixhawk
    cruise_ms: float = 2.5               # must be <= SafetyConfig.max_speed_ms
    climb_ms: float = 1.0                # PLACEHOLDER (WP_SPD_UP)
    descend_ms: float = 0.75             # PLACEHOLDER (WP_SPD_DN)
    dwell_s: float = 20.0                # PLACEHOLDER centering + (future) spray per target
    battery_usable_s: float = 600.0      # PLACEHOLDER measured hover time, F450 + payload
    battery_reserve_frac: float = 0.30
    tank_litres: float = TANK_LITRES_DEFAULT
    tank_reserve_ml: float = 0.0
    geofence_margin_m: float = 3.0       # keep targets this far inside the fence (centering nudges)
    conf_min: float = CONF_MIN
    max_canopy_h_m: Optional[float] = None
    drone_horizontal_sigma_m: float = 1.5    # PLACEHOLDER: ~0.02 with RTK fix
    search_sigma_k: float = 2.0
    camera_hfov_deg: Optional[float] = None  # PLACEHOLDER live camera horizontal FOV
    max_map_age_days: Optional[float] = 14.0
    two_opt_max_passes: int = 50


@dataclass
class PlannedTarget:
    target: Target
    ground_rel_m: float      # DTM(target) - DTM(home)
    obstacle_rel_m: float    # max DSM near target - DTM(home)
    alt_m: float             # approach waypoint, relative to home
    agl_m: float             # alt_m - ground_rel_m
    dose_ml: float

    @property
    def xy(self):
        return self.target.x, self.target.y


@dataclass
class Leg:
    cost_s: float
    transit_alt_m: Optional[float]   # None = straight leg is clear
    feasible: bool
    why: str = ""


@dataclass
class Sortie:
    waypoints: List[dict]            # upload_mission format
    items: List[dict]                # metadata per waypoint (same order)
    target_ids: List[str]
    dose_ml: float
    est_time_s: float
    return_transit_alt_m: Optional[float]
    gate_precheck: dict
    zone_bounds: Tuple[float, float, float, float]

    def upload_mission_args(self) -> dict:
        return {"waypoints": self.waypoints, "rtl_at_end": True}


@dataclass
class Plan:
    home: dict
    limits: dict
    config: dict
    sorties: List[Sortie]
    excluded: List[dict]
    warnings: List[str]

    def to_dict(self) -> dict:
        return {
            "schema": "pds.map_route.v0",
            "home": self.home,
            "limits": self.limits,
            "config": self.config,
            "no_sprayer_commands": True,
            "altitude_frame": "metres relative to home (MAV_FRAME_GLOBAL_RELATIVE_ALT)",
            "sorties": [dict(asdict(s), upload_mission_args=s.upload_mission_args())
                        for s in self.sorties],
            "excluded": self.excluded,
            "warnings": self.warnings,
        }


def check_home_matches(plan_home: dict, actual_home: dict, tol_m: float = 3.0) -> List[str]:
    """Every altitude in a plan is relative to the home the planner ASSUMED.
    If the drone arms somewhere else, every height is wrong by the ground
    difference. Call this with the vehicle's real home before uploading."""
    if actual_home is None:
        return ["vehicle home unknown"]
    from safety_gate import _dist_m
    d = _dist_m(plan_home["lat"], plan_home["lon"], actual_home["lat"], actual_home["lon"])
    return [] if d <= tol_m else [f"vehicle home is {d:.1f} m from the planned home "
                                  f"(> {tol_m} m): re-plan from the real home"]


class RoutePlanner:
    def __init__(self, odm_map, safety_cfg, cfg: Optional[RouteConfig] = None,
                 home_latlon: Tuple[float, float] = None, spray_zones=None, no_fly=None):
        """spray_zones / no_fly: shapely geometries in lon/lat (GeoJSON order)."""
        if home_latlon is None:
            raise MapError("home (lat, lon) is required: every altitude is relative to it")
        if odm_map.dtm is None:
            raise MapError("route planning needs the DTM (run ODM with --dtm)")
        self.m = odm_map
        self.s = safety_cfg
        self.cfg = cfg or RouteConfig()
        self.warnings: List[str] = []
        self.home_lat, self.home_lon = home_latlon
        self.hx, self.hy = odm_map.geo.from_latlon(*home_latlon)
        g = odm_map.dtm.elev(self.hx, self.hy)
        if g is None:
            raise MapError("home is outside the DTM: cannot relate heights to it")
        self.home_ground = g
        def to_map(coords):            # (N, 2) lon/lat -> (N, 2) map metres
            x, y = odm_map.geo._from_ll.transform(coords[:, 0], coords[:, 1])
            return np.column_stack([x, y])
        self.spray_zones = [shapely.transform(z, to_map) for z in (spray_zones or [])]
        self.no_fly = [shapely.transform(z, to_map) for z in (no_fly or [])]
        if odm_map.dsm is None:
            self.warnings.append("no DSM: obstacle clearance uses the DTM only (trees and "
                                 "sheds are invisible to the planner)")
        if self.cfg.cruise_ms > self.s.max_speed_ms:
            raise MapError(f"cruise_ms {self.cfg.cruise_ms} > gate max_speed_ms {self.s.max_speed_ms}")
        if not (self.s.min_goto_alt_m <= self.cfg.takeoff_alt_m <= self.s.max_alt_m):
            raise MapError("takeoff_alt_m outside the gate's altitude band")
        if self.cfg.rtl_alt_m > self.s.max_alt_m:
            self.warnings.append(f"rtl_alt_m {self.cfg.rtl_alt_m} > max_alt_m {self.s.max_alt_m}: "
                                 "RTL will climb above the LLM ceiling")
        self._leg_cache: Dict[tuple, Leg] = {}

    # =====================================================================
    # Heights
    # =====================================================================
    def _surface_rel(self, x: float, y: float, half: float) -> Optional[float]:
        """Tallest surface (DSM, else DTM) near (x, y), relative to home ground."""
        src = self.m.dsm or self.m.dtm
        z = src.max_in_box(x, y, half)
        return None if z is None else z - self.home_ground

    def _ceil(self, a: float) -> float:
        return math.ceil(a * 2.0) / 2.0          # 0.5 m steps, always up

    # =====================================================================
    # Step 1-2: targets -> planned targets / excluded
    # =====================================================================
    def prepare(self, targets: Sequence[Target]):
        c, s = self.cfg, self.s
        planned, excluded = [], []
        fence = s.geofence_radius_m - c.geofence_margin_m

        def drop(t, why):
            excluded.append({"target_id": t.target_id, "lat": t.lat, "lon": t.lon, "reason": why})

        for t in targets:
            if t.confidence < c.conf_min:
                drop(t, f"confidence {t.confidence:.2f} < {c.conf_min}")
                continue
            v = dose_ml(t.area_cm2)
            if v is None:
                drop(t, f"area {t.area_cm2} cm2 below dose threshold")
                continue
            if c.max_canopy_h_m is not None and t.canopy_h_m is not None \
                    and t.canopy_h_m > c.max_canopy_h_m:
                drop(t, f"canopy {t.canopy_h_m:.2f} m > max_canopy_h_m {c.max_canopy_h_m}")
                continue
            p = Point(t.x, t.y)
            if self.spray_zones and not any(z.contains(p) for z in self.spray_zones):
                drop(t, "outside spray zone")
                continue
            if any(z.intersects(p) for z in self.no_fly):
                drop(t, "inside no-fly zone")
                continue
            d_home = math.hypot(t.x - self.hx, t.y - self.hy)
            if d_home > fence:
                drop(t, f"{d_home:.0f} m from home > geofence {s.geofence_radius_m:.0f} m "
                        f"minus {c.geofence_margin_m:.0f} m margin")
                continue
            g = self.m.ground_rel(t.x, t.y, self.home_ground)
            if g is None:
                drop(t, "no DTM height at target")
                continue
            obst = self._surface_rel(t.x, t.y, c.target_clearance_radius_m)
            obst = g if obst is None else max(obst, g)
            alt = max(g + c.approach_agl_m, obst + c.obstacle_clearance_m)
            if alt < s.min_goto_alt_m:
                alt = s.min_goto_alt_m          # raising is safe; lowering never is
            alt = self._ceil(alt)
            agl = alt - g
            if alt > s.max_alt_m:
                drop(t, f"approach alt {alt:.1f} m (ground {g:+.1f} m vs home) > max_alt_m "
                        f"{s.max_alt_m}")
                continue
            if agl > c.max_approach_agl_m:
                drop(t, f"camera would be {agl:.1f} m above ground (> max_approach_agl_m "
                        f"{c.max_approach_agl_m}): ground {g:+.1f} m vs home or obstacle nearby")
                continue
            planned.append(PlannedTarget(t, round(g, 2), round(obst, 2), alt, round(agl, 2), v))
        return planned, excluded

    # =====================================================================
    # Step 3-4: legs
    # =====================================================================
    def _crosses_no_fly(self, a, b) -> bool:
        if not self.no_fly:
            return False
        ln = LineString([a, b]) if a != b else Point(a)
        return any(z.intersects(ln) for z in self.no_fly)

    def leg(self, a: Tuple[float, float, float], b: Tuple[float, float, float]) -> Leg:
        """Fly from a=(x, y, alt) to b. Cached."""
        key = (round(a[0], 3), round(a[1], 3), a[2], round(b[0], 3), round(b[1], 3), b[2])
        if key in self._leg_cache:
            return self._leg_cache[key]
        c = self.cfg
        (ax, ay, aa), (bx, by, ba) = a, b
        d = math.hypot(bx - ax, by - ay)
        if self._crosses_no_fly((ax, ay), (bx, by)):
            res = Leg(BIG, None, False, "crosses no-fly zone")
            self._leg_cache[key] = res
            return res
        n = max(2, int(math.ceil(d / c.leg_sample_step_m)) + 1)
        need = -1e9
        clear = True
        for i in range(n):
            f = i / (n - 1)
            px, py = ax + f * (bx - ax), ay + f * (by - ay)
            surf = self._surface_rel(px, py, c.leg_corridor_half_width_m)
            if surf is None:
                res = Leg(BIG, None, False, "leg crosses ground with no elevation data")
                self._leg_cache[key] = res
                return res
            req = surf + c.obstacle_clearance_m
            need = max(need, req)
            if aa + f * (ba - aa) < req - 1e-6:
                clear = False
        if clear:
            cost = d / c.cruise_ms + max(0.0, ba - aa) / c.climb_ms + max(0.0, aa - ba) / c.descend_ms
            res = Leg(cost, None, True)
        else:
            t_alt = self._ceil(max(need, aa, ba))
            if t_alt > self.s.max_alt_m:
                res = Leg(BIG, t_alt, False, f"needs transit {t_alt:.1f} m > max_alt_m")
            else:
                cost = (t_alt - aa) / c.climb_ms + d / c.cruise_ms + (t_alt - ba) / c.descend_ms
                res = Leg(cost, t_alt, True)
        self._leg_cache[key] = res
        return res

    def _home_start(self):
        return (self.hx, self.hy, self.cfg.takeoff_alt_m)

    def _node(self, p: PlannedTarget):
        return (p.target.x, p.target.y, p.alt_m)

    def return_leg(self, p: PlannedTarget) -> Leg:
        """RTL from a target: ArduPilot climbs to RTL_ALT if lower, flies
        straight home at that height, then lands. We check that straight line
        against the DSM; if it is not clear, a transit waypoint above the last
        target lifts the start of RTL high enough (RTL keeps the current
        height when it is already above RTL_ALT)."""
        c = self.cfg
        start = max(p.alt_m, c.rtl_alt_m)
        flat = self.leg((p.target.x, p.target.y, start), (self.hx, self.hy, start))
        if not flat.feasible:
            return flat
        top = flat.transit_alt_m or start
        d = math.hypot(p.target.x - self.hx, p.target.y - self.hy)
        cost = (top - p.alt_m) / c.climb_ms + d / c.cruise_ms + top / c.descend_ms
        # transit_alt_m is set only when RTL's own climb to rtl_alt_m is not
        # enough and we must add a climb-in-place waypoint before RTL.
        return Leg(cost, flat.transit_alt_m, True)

    # =====================================================================
    # Step 5: ordering and sorties
    # =====================================================================
    def _matrix(self, pts: List[PlannedTarget]):
        """C[i][j], node 0 = home. Includes dwell at the arrival target."""
        n = len(pts)
        C = [[0.0] * (n + 1) for _ in range(n + 1)]
        for j in range(1, n + 1):
            lg = self.leg(self._home_start(), self._node(pts[j - 1]))
            C[0][j] = (lg.cost_s + self.cfg.dwell_s) if lg.feasible else BIG
            rl = self.return_leg(pts[j - 1])
            C[j][0] = rl.cost_s if rl.feasible else BIG
        for i in range(1, n + 1):
            for j in range(1, n + 1):
                if i != j:
                    lg = self.leg(self._node(pts[i - 1]), self._node(pts[j - 1]))
                    C[i][j] = (lg.cost_s + self.cfg.dwell_s) if lg.feasible else BIG
        return C

    @staticmethod
    def tour_cost(C, order: List[int]) -> float:
        if not order:
            return 0.0
        cost = C[0][order[0]] + C[order[-1]][0]
        for a, b in zip(order, order[1:]):
            cost += C[a][b]
        return cost

    @staticmethod
    def nearest_neighbour(C, nodes: List[int]) -> List[int]:
        left, order, cur = list(nodes), [], 0
        while left:
            nxt = min(left, key=lambda j: (C[cur][j], j))   # ties -> lowest index: deterministic
            order.append(nxt)
            left.remove(nxt)
            cur = nxt
        return order

    def two_opt(self, C, order: List[int]) -> List[int]:
        """Classic 2-opt, but scoring the WHOLE tour each time because costs
        are asymmetric (reversing a segment changes uphill into downhill).
        O(n^3) per pass: fine for the ~25 weeds per paddock in LIVE-PIPELINE."""
        best = list(order)
        best_cost = self.tour_cost(C, best)
        for _ in range(self.cfg.two_opt_max_passes):
            improved = False
            for i in range(len(best) - 1):
                for j in range(i + 1, len(best)):
                    cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                    cc = self.tour_cost(C, cand)
                    if cc < best_cost - 1e-9:
                        best, best_cost, improved = cand, cc, True
            if not improved:
                break
        return best

    def _items_for(self, pts, order: List[int]) -> int:
        """Waypoint count the sortie would upload (targets + transit points)."""
        n, prev = 0, self._home_start()
        for k in order:
            nd = self._node(pts[k - 1])
            lg = self.leg(prev, nd)
            if lg.transit_alt_m is not None:
                n += 2 if lg.transit_alt_m != nd[2] else 1
            n += 1
            prev = nd
        if order:
            rl = self.return_leg(pts[order[-1] - 1])
            if rl.transit_alt_m is not None:
                n += 1
        return n

    def plan(self, targets: Sequence[Target], now: Optional[datetime] = None) -> Plan:
        c, s = self.cfg, self.s
        pts, excluded = self.prepare(targets)
        self._map_age_warning(targets, now)
        budget = c.battery_usable_s * (1.0 - c.battery_reserve_frac)
        tank_ml = c.tank_litres * 1000.0 - c.tank_reserve_ml
        C = self._matrix(pts)

        # Targets we cannot even do alone (out and straight back) are dropped.
        doable = []
        for k in range(1, len(pts) + 1):
            p = pts[k - 1]
            solo = C[0][k] + C[k][0]
            if C[0][k] >= BIG or C[k][0] >= BIG:
                lg = self.leg(self._home_start(), self._node(p))
                why = lg.why if not lg.feasible else self.return_leg(p).why
                excluded.append(self._ex(p, f"no clear direct path to/from home ({why})"))
            elif solo > budget:
                excluded.append(self._ex(p, f"needs {solo:.0f} s alone > battery budget {budget:.0f} s"))
            elif p.dose_ml > tank_ml:
                excluded.append(self._ex(p, "dose larger than tank"))
            else:
                doable.append(k)

        # Route first ...
        order = self.two_opt(C, self.nearest_neighbour(C, doable)) if doable else []
        # ... cluster second.
        groups, cur, t_acc, dose_acc = [], [], 0.0, 0.0
        for k in order:
            p = pts[k - 1]
            step = C[cur[-1]][k] if cur else C[0][k]
            fits = (cur and step < BIG
                    and t_acc + step + C[k][0] <= budget
                    and dose_acc + p.dose_ml <= tank_ml
                    and self._items_for(pts, cur + [k]) <= s.max_mission_items)
            if cur and not fits:
                groups.append(cur)
                cur, t_acc, dose_acc = [], 0.0, 0.0
                step = C[0][k]
            cur.append(k)
            t_acc += step
            dose_acc += p.dose_ml
        if cur:
            groups.append(cur)

        sorties = []
        for g in groups:
            g2 = self.two_opt(C, g)
            if (self.tour_cost(C, g2) > budget or
                    self._items_for(pts, g2) > s.max_mission_items):
                g2 = g
            sorties.append(self._build_sortie(pts, g2, C))
        return Plan(
            home={"lat": self.home_lat, "lon": self.home_lon,
                  "ground_elev_m": round(self.home_ground, 3),
                  "note": "all alt_m are relative to THIS point; check_home_matches() before upload"},
            limits={"max_alt_m": s.max_alt_m, "min_goto_alt_m": s.min_goto_alt_m,
                    "geofence_radius_m": s.geofence_radius_m, "max_speed_ms": s.max_speed_ms,
                    "max_mission_items": s.max_mission_items},
            config=asdict(c), sorties=sorties, excluded=excluded, warnings=self.warnings)

    def _ex(self, p: PlannedTarget, why: str) -> dict:
        return {"target_id": p.target.target_id, "lat": p.target.lat,
                "lon": p.target.lon, "reason": why}

    def _map_age_warning(self, targets, now):
        c = self.cfg
        if c.max_map_age_days is None or not targets:
            return
        stamp = (targets[0].source or {}).get("map_captured_at")
        if not stamp:
            self.warnings.append("map capture date unknown: weeds may have grown, "
                                 "been grazed or been sprayed since")
            return
        try:
            d0 = date.fromisoformat(str(stamp)[:10])
        except ValueError:
            self.warnings.append(f"unreadable map_captured_at {stamp!r}")
            return
        today = (now or datetime.now()).date()
        age = (today - d0).days
        if age > c.max_map_age_days:
            self.warnings.append(f"map is {age} days old (> {c.max_map_age_days:.0f}): "
                                 "re-fly the survey or expect misses")

    # =====================================================================
    # Step 6: waypoints, hand-off metadata, gate pre-check
    # =====================================================================
    def _wp(self, x, y, alt):
        lat, lon = self.m.geo.to_latlon(x, y)
        return {"lat": round(lat, 8), "lon": round(lon, 8), "alt_m": round(float(alt), 2)}

    def handoff(self, p: PlannedTarget) -> dict:
        """What the visual centering loop needs when the drone arrives."""
        c = self.cfg
        sigma = math.hypot(p.target.georef_sigma_m, c.drone_horizontal_sigma_m)
        radius = round(c.search_sigma_k * sigma, 2)
        h = {"mode": "visual_centering", "expected_lat": p.target.lat,
             "expected_lon": p.target.lon, "expected_ground_rel_home_m": p.ground_rel_m,
             "approach_agl_m": p.agl_m, "search_radius_m": radius,
             "label": p.target.label, "area_cm2": p.target.area_cm2}
        if c.camera_hfov_deg:
            half = p.agl_m * math.tan(math.radians(c.camera_hfov_deg) / 2.0)
            h["camera_half_footprint_m"] = round(half, 2)
            h["target_probably_in_view"] = radius <= half
        return h

    def _build_sortie(self, pts, order: List[int], C) -> Sortie:
        wps, meta, prev = [], [], self._home_start()
        for k in order:
            p = pts[k - 1]
            nd = self._node(p)
            lg = self.leg(prev, nd)
            if lg.transit_alt_m is not None:
                wps.append(self._wp(prev[0], prev[1], lg.transit_alt_m))
                meta.append({"kind": "transit_climb", "why": "clear obstacle on next leg"})
                if lg.transit_alt_m != nd[2]:
                    wps.append(self._wp(nd[0], nd[1], lg.transit_alt_m))
                    meta.append({"kind": "transit_over_target"})
            wps.append(self._wp(*nd))
            meta.append({"kind": "target", "target_id": p.target.target_id,
                         "dose_ml": round(p.dose_ml, 3), "confidence": p.target.confidence,
                         "ground_rel_home_m": p.ground_rel_m, "agl_m": p.agl_m,
                         "handoff": self.handoff(p)})
            prev = nd
        rl = self.return_leg(pts[order[-1] - 1])
        ret_alt = None
        if rl.transit_alt_m is not None:
            ret_alt = rl.transit_alt_m
            wps.append(self._wp(prev[0], prev[1], ret_alt))
            meta.append({"kind": "transit_climb", "why": "clear obstacles on the RTL leg"})
        lats = [w["lat"] for w in wps]
        lons = [w["lon"] for w in wps]
        bounds = (min(lats) - 1e-4, min(lons) - 1e-4, max(lats) + 1e-4, max(lons) + 1e-4)
        verdict = FlightGate(self.s).check_mission(wps, {"lat": self.home_lat, "lon": self.home_lon})
        return Sortie(
            waypoints=wps, items=meta, target_ids=[pts[k - 1].target.target_id for k in order],
            dose_ml=round(sum(pts[k - 1].dose_ml for k in order), 3),
            est_time_s=round(self.tour_cost(C, order), 1), return_transit_alt_m=ret_alt,
            gate_precheck={"allowed": verdict.allowed, "reasons": verdict.reasons},
            zone_bounds=bounds)
