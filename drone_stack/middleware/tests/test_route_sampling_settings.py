"""Issue #29 (part 2): the leg sampling settings must not let an obstacle slip
through between samples.

The leg check walks along a leg every `leg_sample_step_m` and looks
`leg_corridor_half_width_m` either side of each sample. If the step is longer
than the box it looks in (2 x half width), there are gaps between samples
where nothing is read. A negative or zero step is worse: the walk collapses
to the two end points (or divides by zero).

Synthetic ODM scene only (map_synth): no vehicle, no SITL, no hardware.
"""
import math

import pytest

pytest.importorskip("rasterio")
pytest.importorskip("pyproj")

from map_georef import Target  # noqa: E402
from map_rasters import OdmMap, OdmPaths  # noqa: E402
from map_route import RouteConfig, RoutePlanner  # noqa: E402
from map_synth import build_scene, stack_config_path  # noqa: E402
from map_to_route import route_config  # noqa: E402
from stack_config import load_config  # noqa: E402


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    return build_scene(tmp_path_factory.mktemp("odm29"))


@pytest.fixture(scope="module")
def safety():
    return load_config(str(stack_config_path())).safety


@pytest.fixture
def om(scene):
    with OdmMap(OdmPaths.from_project(scene.root)) as m:
        yield m


def _target(om, scene, e, n, tid):
    x, y = scene.utm(e, n)
    lat, lon = om.geo.to_latlon(x, y)
    return Target(target_id=tid, label="weed", confidence=0.9, x=x, y=y, lat=lat, lon=lon,
                  ground_elev_m=om.dtm.elev(x, y), canopy_h_m=0.05, area_cm2=700.0,
                  georef_sigma_m=3.0, source={"map_captured_at": "2026-10-20"})


# (leg_sample_step_m, leg_corridor_half_width_m). The first is the default.
# The rest are the settings Manus/Maya used to fly a leg through the tree,
# plus zero/negative/non-finite values.
SETTINGS = [
    (0.5, 1.5),          # defaults
    (3.0, 1.5),          # step == box width: still contiguous, allowed
    (20.0, 0.4),         # issue #29 reproduction: 20 m steps, 0.8 m box
    (10.0, 1.5),         # step longer than the box
    (-1.0, 1.5),         # negative step: walk collapses to the two end points
    (0.5, -5.0),         # negative half width
    (0.5, 0.0),          # zero half width
    (float("nan"), 1.5),
    (0.5, float("inf")),
]


def _make(step, half):
    return RouteConfig(camera_hfov_deg=90.0, leg_sample_step_m=step,
                       leg_corridor_half_width_m=half)


@pytest.mark.parametrize("step,half", SETTINGS)
def test_no_accepted_sampling_setting_flies_through_the_tree(om, scene, safety, step, half):
    """Whatever sampling settings the planner accepts, a leg straight over the
    8 m tree must clear tree top + obstacle_clearance_m. Settings that would
    let it fly through must be refused when the config is built."""
    try:
        c = _make(step, half)
    except ValueError:
        return                                  # refused up front: safe
    p = RoutePlanner(om, safety, c, home_latlon=scene.home_latlon)
    pts, _ = p.prepare([_target(om, scene, 30.0, 30.0, "A"),
                        _target(om, scene, 30.0, 18.0, "B")])
    leg = p.leg(p._node(pts[0]), p._node(pts[1]))
    te0, tn0, te1, tn1, th = scene.tree
    tree_top_rel = scene.ground(te1, 0) + th - scene.ground(*scene.home_en)
    if leg.feasible:
        assert leg.transit_alt_m is not None, (
            f"step={step}, half={half}: leg planned straight through the tree")
        assert leg.transit_alt_m >= tree_top_rel + c.obstacle_clearance_m


@pytest.mark.parametrize("step,half", [
    (-1.0, -5.0),        # the exact values from issue #29
    (0.0, 1.5),
    (-1.0, 1.5),
    (0.5, 0.0),
    (0.5, -5.0),
    (20.0, 0.4),
    (3.01, 1.5),
    (float("nan"), 1.5),
    (0.5, float("inf")),
])
def test_route_config_rejects_bad_sampling_settings(step, half):
    with pytest.raises(ValueError):
        _make(step, half)


def test_route_config_accepts_defaults_and_contiguous_sampling():
    RouteConfig()
    _make(0.5, 1.5)
    _make(3.0, 1.5)      # step exactly equal to the box width (2 x half)


def test_toml_route_section_is_validated_too():
    """map_to_route builds RouteConfig from the [route] table; bad values in
    the file must be refused there as well, not only in Python."""
    with pytest.raises(ValueError):
        route_config({"leg_sample_step_m": 20.0, "leg_corridor_half_width_m": 0.4})
    with pytest.raises(ValueError):
        route_config({"leg_sample_step_m": -1.0})
    assert math.isclose(route_config({}).leg_sample_step_m, 0.5)
