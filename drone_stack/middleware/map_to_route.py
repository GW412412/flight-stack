"""ODM map -> targets -> waypoint sorties: pipeline + command line.

Contributed by Maya (AI agent, Sesame). Experimental. Design:
docs/MAP-TO-ROUTE.md. Example settings: config/map_to_route.example.toml.

    python map_to_route.py --config ../../config/map_to_route.example.toml \
        --project /path/to/odm/project --home=-35.363261,149.165230 --out plan/

Writes into --out:
    targets.geojson              every georeferenced target (spray_mission format)
    sortie_01_targets.geojson    the targets of each sortie, so the EXISTING
    sortie_02_targets.geojson    spray_mission.generate_spray_plan can read any one
    mission_plan.json            sorties with upload_mission-ready waypoints,
                                 hand-off metadata, exclusions with reasons,
                                 warnings and the safety-gate pre-check

It never connects to a vehicle, never uploads, never arms, and never emits a
sprayer command. Limits come from the stack config (llm_stack.*.toml), the
same file the MCP server and safety gate use.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import fields
from pathlib import Path
from typing import List, Optional

from shapely.geometry import shape

from map_detect import ColourBlobDetector, UltralyticsSegAdapter
from map_georef import detect_on_map, georeference, write_geojson
from map_rasters import OdmMap, OdmPaths
from map_route import RouteConfig, RoutePlanner
from stack_config import load_config


def load_zones(path: Optional[str]) -> List:
    """GeoJSON file of Polygon / MultiPolygon features (lon/lat) -> shapely."""
    if not path:
        return []
    with open(path) as f:
        data = json.load(f)
    feats = data.get("features", [data] if data.get("type") == "Feature" else [])
    return [shape(ft["geometry"]) for ft in feats]


def make_detector(dcfg: dict):
    kind = dcfg.get("kind", "colour_blob")
    if kind == "colour_blob":
        keys = ("exg_min", "min_area_px", "max_area_px", "max_canopy_h_m", "min_canopy_h_m", "label")
        return ColourBlobDetector(**{k: dcfg[k] for k in keys if k in dcfg})
    if kind == "ultralytics_seg":
        keys = ("channels", "conf", "imgsz", "device", "input_order", "chm_full_scale_m")
        return UltralyticsSegAdapter(dcfg.get("weights"), **{k: dcfg[k] for k in keys if k in dcfg})
    raise ValueError(f"unknown detector kind {kind!r}")


def route_config(rcfg: dict) -> RouteConfig:
    names = {f.name for f in fields(RouteConfig)}
    unknown = set(rcfg) - names
    if unknown:
        raise ValueError(f"unknown [route] keys: {sorted(unknown)}")
    return RouteConfig(**rcfg)


def run(cfg: dict, project: Optional[str] = None, home=None, out_dir: Optional[str] = None,
        detector=None, now=None) -> dict:
    """Library entry point (the CLI and the tests both call this)."""
    m = cfg.get("map", {})
    if project:
        paths = OdmPaths.from_project(project)
    else:
        paths = OdmPaths(ortho=Path(m["ortho"]), dsm=Path(m["dsm"]) if m.get("dsm") else None,
                         dtm=Path(m["dtm"]) if m.get("dtm") else None)
    stack = load_config(cfg.get("limits", {}).get("stack_config"))
    home = home or tuple(cfg.get("home", {}).get("latlon", ()))
    t = cfg.get("tiles", {})
    det = detector or make_detector(cfg.get("detector", {}))
    z = cfg.get("zones", {})
    with OdmMap(paths) as om:
        pairs = detect_on_map(om, det, t.get("size_px", 640), t.get("overlap_px", 128),
                              with_chm=om.dsm_ds is not None and om.dtm_ds is not None)
        targets = georeference(om, pairs, georef_sigma_m=float(m.get("horizontal_sigma_m", 3.0)),
                               detector_name=getattr(det, "name", type(det).__name__),
                               map_captured_at=m.get("captured_at"))
        planner = RoutePlanner(om, stack.safety, route_config(cfg.get("route", {})),
                               home_latlon=home, spray_zones=load_zones(z.get("spray_zone")),
                               no_fly=load_zones(z.get("no_fly")))
        plan = planner.plan(targets, now=now)
    result = plan.to_dict()
    result["target_count"] = len(targets)
    result["stack_profile"] = stack.profile
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_geojson(out / "targets.geojson", targets)
        by_id = {tg.target_id: tg for tg in targets}
        for i, s in enumerate(plan.sorties, 1):
            write_geojson(out / f"sortie_{i:02d}_targets.geojson",
                          [by_id[tid] for tid in s.target_ids], {"sortie": i})
        with open(out / "mission_plan.json", "w") as f:
            json.dump(result, f, indent=1)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--project", help="ODM project dir (overrides [map] paths)")
    ap.add_argument("--home", help="lat,lon of the take-off point; write --home=-35.36,149.17 (with =) because a negative latitude otherwise reads as an option")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    with open(a.config, "rb") as f:
        cfg = tomllib.load(f)
    home = tuple(float(v) for v in a.home.split(",")) if a.home else None
    r = run(cfg, a.project, home, a.out)
    print(f"{r['target_count']} targets, {len(r['sorties'])} sorties, "
          f"{len(r['excluded'])} excluded, {len(r['warnings'])} warnings -> {a.out}")
    for i, s in enumerate(r["sorties"], 1):
        print(f"  sortie {i}: {len(s['target_ids'])} targets, {len(s['waypoints'])} waypoints, "
              f"{s['dose_ml']} mL, ~{s['est_time_s']:.0f} s, gate pre-check "
              f"{'OK' if s['gate_precheck']['allowed'] else 'REFUSED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
