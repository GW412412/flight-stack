"""Map-to-route prototype: route planner.

Contributed by Maya (AI agent, Sesame). Synthetic ODM project only.
Limits come from the real config/llm_stack.pixhawk.toml (20 m / 60 m / 3 m/s).
"""
import dataclasses
import math
from datetime import datetime

import pytest

pytest.importorskip("rasterio")
pytest.importorskip("pyproj")

from shapely.geometry import box  # noqa: E402

from map_detect import ColourBlobDetector  # noqa: E402
from map_georef import Target, detect_on_map, georeference  # noqa: E402
from map_rasters import MapError, OdmMap, OdmPaths  # noqa: E402
from map_route import RouteConfig, RoutePlanner, check_home_matches  # noqa: E402
from map_synth import build_scene, stack_config_path  # noqa: E402
from safety_gate import FlightGate, _dist_m  # noqa: E402
from stack_config import load_config  # noqa: E402

NOW = datetime(2026, 10, 25, 5, 0)


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    return build_scene(tmp_path_factory.mktemp("odm"))


@pytest.fixture(scope="module")
def safety():
    return load_config(str(stack_config_path())).safety


@pytest.fixture
def om(scene):
    with OdmMap(OdmPaths.from_project(scene.root)) as m:
        yield m


@pytest.fixture
def targets(om):
    pairs = detect_on_map(om, ColourBlobDetector(min_area_px=8, max_canopy_h_m=0.6), 256, 64)
    return georeference(om, pairs, georef_sigma_m=3.0, detector_name="colour_blob",
                        map_captured_at="2026-10-20")


def cfg(**kw):
    base = dict(camera_hfov_deg=90.0)
    base.update(kw)
    return RouteConfig(**base)


def planner(om, scene, safety, **kw):
    return RoutePlanner(om, safety, cfg(**kw), home_latlon=scene.home_latlon)


def make_target(om, scene, e, n, tid="S1", conf=0.9, area=700.0, captured="2026-10-20"):
    x, y = scene.utm(e, n)
    lat, lon = om.geo.to_latlon(x, y)
    return Target(target_id=tid, label="weed", confidence=conf, x=x, y=y, lat=lat, lon=lon,
                  ground_elev_m=om.dtm.elev(x, y), canopy_h_m=0.05, area_cm2=area,
                  georef_sigma_m=3.0, source={"map_captured_at": captured})


def target_items(sortie):
    return [m for m in sortie.items if m["kind"] == "target"]


# ------------------------------------------------------------- basic plan

def test_plan_one_sortie_every_reachable_weed(om, scene, safety, targets):
    plan = planner(om, scene, safety).plan(targets, now=NOW)
    assert len(targets) == len(scene.weeds)
    assert len(plan.sorties) == 1
    assert len(plan.sorties[0].target_ids) == len(scene.weeds) - 1
    assert len(plan.excluded) == 1 and "geofence" in plan.excluded[0]["reason"]   # w8


def test_altitudes_are_terrain_relative_to_home(om, scene, safety, targets):
    plan = planner(om, scene, safety).plan(targets, now=NOW)
    s = plan.sorties[0]
    for wp, m in zip(s.waypoints, s.items):
        if m["kind"] != "target":
            continue
        x, y = om.geo.from_latlon(wp["lat"], wp["lon"])
        e = x - scene.x0
        ground_rel = scene.slope * (e - scene.home_en[0])
        assert m["ground_rel_home_m"] == pytest.approx(ground_rel, abs=0.03)
        assert wp["alt_m"] == pytest.approx(math.ceil((ground_rel + 6.0) * 2) / 2, abs=0.01)
        assert 6.0 <= m["agl_m"] <= 12.0


def test_every_sortie_passes_the_real_safety_gate(om, scene, safety, targets):
    plan = planner(om, scene, safety).plan(targets, now=NOW)
    home = {"lat": scene.home_latlon[0], "lon": scene.home_latlon[1]}
    for s in plan.sorties:
        assert s.gate_precheck["allowed"], s.gate_precheck
        v = FlightGate(safety).check_mission(s.waypoints, home)      # independent re-check
        assert v.allowed, v.reasons
        for wp in s.waypoints:
            assert safety.min_goto_alt_m <= wp["alt_m"] <= safety.max_alt_m
            assert _dist_m(home["lat"], home["lon"], wp["lat"], wp["lon"]) <= safety.geofence_radius_m
        assert set(s.upload_mission_args()) == {"waypoints", "rtl_at_end"}
        assert all(set(w) == {"lat", "lon", "alt_m"} for w in s.waypoints)


def test_every_leg_clears_the_dsm(om, scene, safety, targets):
    p = planner(om, scene, safety)
    plan = p.plan(targets, now=NOW)
    c = p.cfg
    pts = [(p.hx, p.hy, c.takeoff_alt_m)]
    for wp in plan.sorties[0].waypoints:
        x, y = om.geo.from_latlon(wp["lat"], wp["lon"])
        pts.append((x, y, wp["alt_m"]))
    for (ax, ay, aa), (bx, by, ba) in zip(pts, pts[1:]):
        d = math.hypot(bx - ax, by - ay)
        for i in range(int(d / 0.5) + 2):
            f = min(1.0, i * 0.5 / d) if d else 0.0
            surf = om.dsm.max_in_box(ax + f * (bx - ax), ay + f * (by - ay),
                                     c.leg_corridor_half_width_m) - p.home_ground
            assert aa + f * (ba - aa) >= surf + c.obstacle_clearance_m - 0.01


def test_tree_forces_transit_waypoints(om, scene, safety):
    p = planner(om, scene, safety)
    a = make_target(om, scene, 30.0, 30.0, "A")
    b = make_target(om, scene, 30.0, 18.0, "B")
    pts, _ = p.prepare([a, b])
    leg = p.leg(p._node(pts[0]), p._node(pts[1]))
    te0, tn0, te1, tn1, th = scene.tree
    tree_top_rel = scene.ground(te1, 0) + th - scene.ground(*scene.home_en)
    assert leg.feasible and leg.transit_alt_m >= tree_top_rel + 3.0
    # a leg in open pasture needs no transit
    c1, c2 = make_target(om, scene, 8, 6, "C"), make_target(om, scene, 20, 9, "D")
    q, _ = p.prepare([c1, c2])
    assert p.leg(p._node(q[0]), p._node(q[1])).transit_alt_m is None


def test_obstacle_too_tall_makes_leg_infeasible(tmp_path, safety):
    sc = build_scene(tmp_path, tree_height=19.0)
    with OdmMap(OdmPaths.from_project(sc.root)) as om:
        p = RoutePlanner(om, safety, cfg(), home_latlon=sc.home_latlon)
        pts, _ = p.prepare([make_target(om, sc, 30, 30, "A"), make_target(om, sc, 30, 18, "B")])
        leg = p.leg(p._node(pts[0]), p._node(pts[1]))
        assert not leg.feasible and "max_alt_m" in leg.why


def test_rtl_leg_gets_a_climb_waypoint_when_rtl_alt_is_not_enough(om, scene, safety):
    # target straight behind the tree as seen from home
    t = make_target(om, scene, 36.4, 29.0, "BEHIND")
    plan = planner(om, scene, safety, rtl_alt_m=15.0).plan([t], now=NOW)
    s = plan.sorties[0]
    assert s.items[-1]["kind"] == "transit_climb" and "RTL" in s.items[-1]["why"]
    assert s.return_transit_alt_m == s.waypoints[-1]["alt_m"] > 15.0
    assert s.gate_precheck["allowed"]


# ---------------------------------------------------------------- ordering

def test_costs_are_asymmetric_on_a_hill(om, scene, safety):
    p = planner(om, scene, safety)
    pts, _ = p.prepare([make_target(om, scene, 5, 5, "lo"), make_target(om, scene, 40, 5, "hi")])
    up = p.leg(p._node(pts[0]), p._node(pts[1]))
    down = p.leg(p._node(pts[1]), p._node(pts[0]))
    rise = pts[1].alt_m - pts[0].alt_m
    d = math.hypot(pts[1].target.x - pts[0].target.x, pts[1].target.y - pts[0].target.y)
    assert up.cost_s == pytest.approx(d / 2.5 + rise / 1.0)
    assert down.cost_s == pytest.approx(d / 2.5 + rise / 0.75)


def test_two_opt_never_worse_than_nearest_neighbour_and_is_deterministic(om, scene, safety, targets):
    p = planner(om, scene, safety)
    pts, _ = p.prepare(targets)
    C = p._matrix(pts)
    nodes = list(range(1, len(pts) + 1))
    nn = p.nearest_neighbour(C, nodes)
    opt = p.two_opt(C, nn)
    assert sorted(opt) == nodes
    assert p.tour_cost(C, opt) <= p.tour_cost(C, nn) + 1e-9
    a = planner(om, scene, safety).plan(targets, now=NOW)
    b = planner(om, scene, safety).plan(targets, now=NOW)
    assert a.sorties[0].target_ids == b.sorties[0].target_ids


def test_two_opt_untangles_a_crossed_tour(om, scene, safety):
    p = planner(om, scene, safety)
    ts = [make_target(om, scene, e, n, f"S{i}") for i, (e, n) in
          enumerate([(10, 10), (10, 30), (30, 30), (30, 10)])]
    pts, _ = p.prepare(ts)
    C = p._matrix(pts)
    crossed = [1, 3, 2, 4]          # (10,10)->(30,30)->(10,30)->(30,10): an X
    assert p.tour_cost(C, p.two_opt(C, crossed)) < p.tour_cost(C, crossed) - 1.0


# ----------------------------------------------------------------- sorties

def test_tank_limit_splits_sorties(om, scene, safety, targets):
    plan = planner(om, scene, safety, tank_litres=0.006).plan(targets, now=NOW)   # 6 mL
    assert len(plan.sorties) == 4            # 7 targets x 2.5 mL, max 2 per sortie
    assert all(s.dose_ml <= 6.0 for s in plan.sorties)
    assert sum(len(s.target_ids) for s in plan.sorties) == 7


def test_battery_limit_splits_sorties(om, scene, safety, targets):
    plan = planner(om, scene, safety, battery_usable_s=150.0,
                   battery_reserve_frac=0.0).plan(targets, now=NOW)
    assert len(plan.sorties) >= 2
    assert all(s.est_time_s <= 150.0 for s in plan.sorties)
    assert sum(len(s.target_ids) for s in plan.sorties) == 7


def test_mission_item_limit_splits_sorties(om, scene, targets, safety):
    small = dataclasses.replace(safety, max_mission_items=3)
    plan = RoutePlanner(om, small, cfg(), home_latlon=scene.home_latlon).plan(targets, now=NOW)
    assert all(len(s.waypoints) <= 3 for s in plan.sorties)
    assert all(s.gate_precheck["allowed"] for s in plan.sorties)


# ------------------------------------------------------------------ filters

def test_target_too_high_above_home_is_excluded_not_clamped(om, scene, targets, safety):
    low_ceiling = dataclasses.replace(safety, max_alt_m=10.0)
    plan = RoutePlanner(om, low_ceiling, cfg(takeoff_alt_m=8.0),
                        home_latlon=scene.home_latlon).plan(targets, now=NOW)
    reasons = [e["reason"] for e in plan.excluded]
    assert any("max_alt_m" in r for r in reasons)
    for s in plan.sorties:
        assert all(w["alt_m"] <= 10.0 for w in s.waypoints)
        assert all(m["agl_m"] >= 6.0 for m in target_items(s))


def test_downhill_target_raised_to_min_alt_or_excluded(tmp_path, safety):
    sc = build_scene(tmp_path, slope=-0.15)         # ground FALLS to the east
    with OdmMap(OdmPaths.from_project(sc.root)) as om:
        t = make_target(om, sc, 40.0, 12.0, "DOWN")
        p = RoutePlanner(om, safety, cfg(), home_latlon=sc.home_latlon)
        (pt,), _ = p.prepare([t])
        assert pt.ground_rel_m == pytest.approx(-0.15 * 38, abs=0.03)
        assert pt.alt_m == safety.min_goto_alt_m and pt.agl_m > 6.0
        p2 = RoutePlanner(om, safety, cfg(max_approach_agl_m=7.0), home_latlon=sc.home_latlon)
        planned, ex = p2.prepare([t])
        assert planned == [] and "camera would be" in ex[0]["reason"]


def test_confidence_and_dose_threshold_filters(om, scene, safety):
    p = planner(om, scene, safety)
    planned, ex = p.prepare([make_target(om, scene, 8, 6, "lowconf", conf=0.4),
                             make_target(om, scene, 9, 6, "tiny", area=0.01),
                             make_target(om, scene, 10, 6, "ok")])
    assert [x.target.target_id for x in planned] == ["ok"]
    assert {e["target_id"] for e in ex} == {"lowconf", "tiny"}


def test_spray_zone_and_no_fly_zone(om, scene, safety, targets):
    def ll_box(e0, n0, e1, n1):
        (la0, lo0), (la1, lo1) = scene.latlon(e0, n0), scene.latlon(e1, n1)
        return box(min(lo0, lo1), min(la0, la1), max(lo0, lo1), max(la0, la1))
    plan = RoutePlanner(om, safety, cfg(), home_latlon=scene.home_latlon,
                        spray_zones=[ll_box(0, 0, 25, 50)],
                        no_fly=[ll_box(13, 19, 15, 21)]).plan(targets, now=NOW)  # around w2
    reasons = {e["reason"] for e in plan.excluded}
    assert "outside spray zone" in reasons and "inside no-fly zone" in reasons
    kept = len(plan.sorties[0].target_ids)
    assert kept == 3                         # w1, w3, w7


def test_no_fly_wall_blocks_targets_behind_it(om, scene, safety, targets):
    (la0, lo0), (la1, lo1) = scene.latlon(17, 0), scene.latlon(18, 50)
    wall = box(min(lo0, lo1), min(la0, la1), max(lo0, lo1), max(la0, la1))
    p = RoutePlanner(om, safety, cfg(), home_latlon=scene.home_latlon, no_fly=[wall])
    plan = p.plan(targets, now=NOW)
    blocked = [e for e in plan.excluded if "no clear direct path" in e["reason"]]
    assert len(blocked) == 4                 # w3, w4, w5, w6 (w8 is out of the fence)
    assert all("no-fly" in e["reason"] for e in blocked)
    assert sorted(len(s.target_ids) for s in plan.sorties) == [3]


# ------------------------------------------------------- inputs and checks

def test_without_dsm_planner_warns_and_uses_dtm(tmp_path, safety):
    sc = build_scene(tmp_path, with_dsm=False)
    with OdmMap(OdmPaths.from_project(sc.root)) as om:
        p = RoutePlanner(om, safety, cfg(), home_latlon=sc.home_latlon)
        plan = p.plan([make_target(om, sc, 30, 18, "A")], now=NOW)
        assert any("no DSM" in w for w in plan.warnings)
        assert len(plan.sorties) == 1


def test_bad_inputs_refused(om, scene, safety):
    with pytest.raises(MapError, match="home"):
        RoutePlanner(om, safety, cfg(), home_latlon=None)
    with pytest.raises(MapError, match="outside the DTM"):
        RoutePlanner(om, safety, cfg(), home_latlon=scene.latlon(-20, -20))
    with pytest.raises(MapError, match="cruise"):
        RoutePlanner(om, safety, cfg(cruise_ms=5.0), home_latlon=scene.home_latlon)


def test_home_mismatch_check():
    h = {"lat": -35.363, "lon": 149.165}
    assert check_home_matches(h, {"lat": -35.363, "lon": 149.165}) == []
    assert "re-plan" in check_home_matches(h, {"lat": -35.3631, "lon": 149.165})[0]   # ~11 m
    assert check_home_matches(h, None) == ["vehicle home unknown"]


def test_map_age_warnings(om, scene, safety):
    t = make_target(om, scene, 8, 6)
    old = planner(om, scene, safety).plan([t], now=datetime(2026, 11, 30))
    assert any("days old" in w for w in old.warnings)
    fresh = planner(om, scene, safety).plan([t], now=NOW)
    assert not any("days old" in w for w in fresh.warnings)
    unknown = planner(om, scene, safety).plan([make_target(om, scene, 8, 6, captured=None)], now=NOW)
    assert any("capture date unknown" in w for w in unknown.warnings)


def test_handoff_search_radius_vs_camera_footprint(om, scene, safety):
    plan = planner(om, scene, safety).plan([make_target(om, scene, 8, 6)], now=NOW)
    h = target_items(plan.sorties[0])[0]["handoff"]
    assert h["mode"] == "visual_centering"
    assert h["search_radius_m"] == pytest.approx(2 * math.hypot(3.0, 1.5), abs=0.01)
    assert h["camera_half_footprint_m"] == pytest.approx(h["approach_agl_m"], abs=0.01)  # 90 deg
    assert h["target_probably_in_view"] is False     # 6.7 m radius > ~6.1 m: the honest answer
    rtk = planner(om, scene, safety, drone_horizontal_sigma_m=0.05)
    t2 = make_target(om, scene, 8, 6)
    t2.georef_sigma_m = 0.1
    h2 = target_items(rtk.plan([t2], now=NOW).sorties[0])[0]["handoff"]
    assert h2["target_probably_in_view"] is True
