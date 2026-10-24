"""Map-to-route prototype: end-to-end pipeline and pretend-drone upload.

Contributed by Maya (AI agent, Sesame). Synthetic ODM project only.

1. ODM rasters -> tiles -> detector -> georeference -> route -> files on disk,
   through map_to_route.run(), the same entry point as the command line, and
   back into the EXISTING spray_mission.generate_spray_plan.
2. Each planned sortie uploaded through the real stack
       DroneTools -> FlightGate -> MavlinkHub -> pymavlink UDP -> FakeVehicle
   with the envelope (20 m / 60 m / 3 m/s) taken from the real
   config/llm_stack.pixhawk.toml, then downloaded and compared.

No real aircraft, no SITL binary, no sprayer commands anywhere.
"""
import dataclasses
import json
import socket
import time
from datetime import datetime

import pytest

pytest.importorskip("rasterio")
pytest.importorskip("pyproj")

from drone_tools import DroneTools  # noqa: E402
from fake_vehicle import FakeVehicle  # noqa: E402
from map_route import check_home_matches  # noqa: E402
from map_synth import base_cfg, build_scene, stack_config_path  # noqa: E402
from map_to_route import main as cli_main, route_config, run  # noqa: E402
from mav_node import MavNode  # noqa: E402
from mavlink_hub import MavlinkHub  # noqa: E402
from safety_gate import FlightGate, _dist_m  # noqa: E402
from spray_mission import generate_spray_plan  # noqa: E402
from stack_config import load_config  # noqa: E402

NOW = datetime(2026, 10, 25, 5, 0)
SPRAY_CMDS = ("DO_SET_SERVO", "DO_SET_RELAY", "DO_REPEAT_SERVO", "DO_REPEAT_RELAY", "SPRAYER")


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    return build_scene(tmp_path_factory.mktemp("odm_e2e"))


@pytest.fixture(scope="module")
def planned(scene, tmp_path_factory):
    out = tmp_path_factory.mktemp("plan")
    result = run(base_cfg(scene), home=scene.home_latlon, out_dir=str(out), now=NOW)
    return result, out


# ---------------------------------------------------------------- pipeline
def test_pipeline_end_to_end_writes_a_consistent_plan(scene, planned):
    result, out = planned
    assert (out / "targets.geojson").exists() and (out / "mission_plan.json").exists()
    on_disk = json.loads((out / "mission_plan.json").read_text())
    assert on_disk["schema"] == "pds.map_route.v0" and on_disk["no_sprayer_commands"] is True
    assert len(on_disk["sorties"]) == len(result["sorties"]) >= 1

    feats = json.loads((out / "targets.geojson").read_text())["features"]
    assert len(feats) == result["target_count"]

    planned_ids = [t for s in result["sorties"] for t in s["target_ids"]]
    excluded_ids = [e["target_id"] for e in result["excluded"]]
    assert len(planned_ids) == len(set(planned_ids))                  # no target twice
    assert set(planned_ids).isdisjoint(excluded_ids)
    assert len(planned_ids) + len(excluded_ids) == result["target_count"]

    # Every reachable weed is found once (the overlap weed w7 is not doubled),
    # the 1.5 m shrub is rejected by the height filter, and w8 (~63 m out,
    # beyond the 60 m geofence) is excluded with a reason, not silently lost.
    def near(e, n, tol=0.6):
        lat, lon = scene.latlon(e, n)
        return [f for f in feats
                if _dist_m(lat, lon, f["properties"]["lat"], f["properties"]["lon"]) <= tol]
    for wid, (e, n, _r) in scene.weeds.items():
        assert len(near(e, n)) == 1, wid
    se, sn, _sr, _sh = scene.shrub
    assert near(se, sn, tol=1.0) == []
    assert len(excluded_ids) >= 1 and all(e.get("reason") for e in result["excluded"])


def test_pipeline_sorties_respect_the_envelope_and_carry_no_spray_commands(planned):
    result, _ = planned
    lim = result["limits"]
    for s in result["sorties"]:
        assert s["gate_precheck"]["allowed"], s["gate_precheck"]
        args = s["upload_mission_args"]
        assert args["rtl_at_end"] is True
        assert set(args) == {"waypoints", "rtl_at_end"}
        for w in args["waypoints"]:
            assert set(w) == {"lat", "lon", "alt_m"}
            assert w["alt_m"] <= lim["max_alt_m"] + 1e-6
            assert _dist_m(result["home"]["lat"], result["home"]["lon"],
                           w["lat"], w["lon"]) <= lim["geofence_radius_m"]
        blob = json.dumps(s).upper()
        assert not any(c in blob for c in SPRAY_CMDS)


def test_each_sortie_file_feeds_the_existing_spray_planner(planned):
    result, out = planned
    for i, s in enumerate(result["sorties"], 1):
        wps, bounds, meta = generate_spray_plan(str(out / f"sortie_{i:02d}_targets.geojson"))
        assert len(wps) >= 1
        assert meta["skipped"] == [] or len(wps) + len(meta["skipped"]) >= len(s["target_ids"])


def test_cli_matches_library(scene, tmp_path):
    cfg_path = tmp_path / "cfg.toml"
    c = base_cfg(scene)
    lines = []
    for sect, kv in c.items():
        lines.append(f"[{sect}]")
        for k, v in kv.items():
            lines.append(f"{k} = {json.dumps(v)}")
    cfg_path.write_text("\n".join(lines) + "\n")
    lat, lon = scene.home_latlon
    assert cli_main(["--config", str(cfg_path), f"--home={lat},{lon}",
                     "--out", str(tmp_path / "o")]) == 0
    plan = json.loads((tmp_path / "o" / "mission_plan.json").read_text())
    assert plan["sorties"] and plan["no_sprayer_commands"] is True


def test_example_settings_file_is_valid_and_runs(scene, tmp_path, monkeypatch):
    import tomllib
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    with open(root / "config" / "map_to_route.example.toml", "rb") as f:
        cfg = tomllib.load(f)
    route_config(cfg["route"])                                     # no unknown keys
    assert cfg["limits"]["stack_config"].endswith("llm_stack.pixhawk.toml")
    monkeypatch.chdir(root / "drone_stack" / "middleware")         # relative path as documented
    cfg["map"].update(ortho=str(scene.paths["ortho"]), dsm=str(scene.paths["dsm"]),
                      dtm=str(scene.paths["dtm"]))
    cfg["tiles"] = {"size_px": 256, "overlap_px": 64}
    cfg["detector"]["min_area_px"] = 8
    r = run(cfg, home=scene.home_latlon, now=NOW)
    assert r["sorties"] and r["stack_profile"]


# ----------------------------------------------------------- pretend drone
def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_for(pred, timeout=8.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return False


def _stack(tmp_path, home):
    """Real Pixhawk envelope; only the switches a bench test needs are flipped
    (actuation on, dry run off, echo confirmations, no rate limit)."""
    real = load_config(str(stack_config_path())).safety
    cfg = dataclasses.replace(real, actuation_enabled=True, dry_run=False, confirm_mode="echo",
                              min_action_interval_s=0, max_actions_per_minute=1000)
    port = _free_udp_port()
    node = MavNode(f"udpin:127.0.0.1:{port}", record_dir=None, send_heartbeat=True)
    node.start(wait_heartbeat=False)
    fv = FakeVehicle(out=f"127.0.0.1:{port}", home=home).start()
    gate = FlightGate(cfg)
    hub = MavlinkHub(node, flight_gate=gate, dry_run=False, log_dir=str(tmp_path))
    tools = DroneTools(node, hub, gate, log_dir=str(tmp_path), home_poll_s=0.5)
    assert _wait_for(lambda: node.link_ok() and node.home() is not None), "no link/home"
    return tools, fv, real


def _confirm(fn, **kw):
    r = fn(**kw)
    assert r.get("confirmation_required"), r
    return fn(**kw, confirm_token=r["confirm_token"])


def test_planned_sorties_upload_to_pretend_drone_and_read_back(scene, planned, tmp_path):
    result, _ = planned
    lat, lon = scene.home_latlon
    tools, fv, real = _stack(tmp_path, (lat, lon, scene.ground(*scene.home_en)))
    try:
        assert real.max_alt_m == 20 and real.geofence_radius_m == 60
        assert check_home_matches(result["home"], tools.node.home()) == []
        for s in result["sorties"]:
            wps = s["upload_mission_args"]["waypoints"]
            r = _confirm(tools.upload_mission, **s["upload_mission_args"])
            assert r["ok"] and r["items_total"] == len(wps) + 2, r    # home + wps + RTL
            m = tools.get_mission()
            assert m["ok"] and m["count"] == len(wps) + 2
            for sent, got in zip(wps, m["items"][1:-1]):
                assert got["command"] == "MAV_CMD_NAV_WAYPOINT"
                assert abs(got["lat"] - sent["lat"]) < 2e-7
                assert abs(got["lon"] - sent["lon"]) < 2e-7
                assert abs(got["alt_m"] - sent["alt_m"]) < 0.01
            assert m["items"][-1]["command"] == "MAV_CMD_NAV_RETURN_TO_LAUNCH"
            assert not any(c in it["command"] for it in m["items"] for c in SPRAY_CMDS)
        assert fv.armed is False                                     # upload never arms
    finally:
        tools.close()
        fv.stop()


def test_drone_armed_far_from_planned_home_is_caught(scene, planned, tmp_path):
    """Heights are relative to the planned home. A drone whose home is ~110 m
    away must be flagged by check_home_matches, and the gate must refuse the
    upload because the waypoints fall outside its 60 m geofence."""
    result, _ = planned
    lat, lon = scene.home_latlon
    tools, fv, _ = _stack(tmp_path, (lat + 0.001, lon, scene.ground(*scene.home_en)))
    try:
        warn = check_home_matches(result["home"], tools.node.home())
        assert warn and "re-plan" in warn[0]
        r = tools.upload_mission(**result["sorties"][0]["upload_mission_args"])
        assert r.get("blocked"), r
        assert fv.mission == []
    finally:
        tools.close()
        fv.stop()
