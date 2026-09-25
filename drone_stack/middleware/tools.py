"""Agent tool registry: schemas + validation + dispatch.

Design rules:
  - Every tool is validated against its JSON schema before execution.
  - Tools return plain dicts; the agent never sees raw exceptions.
  - Actuation tools consult SafetyGate. LLM output cannot bypass it.
"""
import json
import time
import jsonschema

from mav_node import MavNode
from safety_gate import SafetyGate
from spray_mission import generate_spray_plan

_node = MavNode()
_gate = SafetyGate()
_started = False


def _ensure_started():
    global _started
    if not _started:
        _node.start()
        _started = True


def get_aircraft_status():
    _ensure_started()
    return _node.get_status()


def run_preflight_checklist():
    _ensure_started()
    status = _node.get_status()
    arm = _gate.can_arm(status)
    return {
        "arm_allowed": arm.allowed,
        "blockers": arm.reasons,
        "status": {k: status.get(k) for k in
                   ("mode", "armed", "battery", "fix_type", "lat", "lon", "rel_alt")},
        "checklist": [
            "props clear" if not status.get("armed") else "PROPS AREA - ARMED",
            "geofence loaded (verify in QGC)",
            "spray tank: WATER ONLY for testing",
            "RTK corrections streaming" if status.get("fix_type", 0) >= 5
                else "WARNING: no RTK (spray will be blocked)",
        ],
    }


def plan_spray_mission(geojson_path, terrain_tiff=None, tank_litres=5.0):
    """Generate + safety-check a spray mission. Returns plan path; never uploads."""
    _ensure_started()
    waypoints, bounds = generate_spray_plan(geojson_path, terrain_tiff, tank_litres)
    verdict = _gate.check_mission(waypoints, bounds)
    if not verdict.allowed:
        return {"error": "mission failed safety check", "reasons": verdict.reasons}
    path = _node.save_qgc_plan(waypoints, f"spray_plan_{int(time.time())}.plan")
    return {"mission_file": path, "waypoint_count": len(waypoints),
            "review": "Import into QGroundControl and verify before upload."}


def request_spray_enable():
    """Spray interlock check -- the actual GPIO fire stays a human action."""
    _ensure_started()
    v = _gate.can_spray(_node.get_status())
    return {"spray_allowed": v.allowed, "blockers": v.reasons,
            "note": "Physical valve still requires operator switch. This gate is advisory."}


TOOLS = [
    {"type": "function", "function": {
        "name": "get_aircraft_status",
        "description": "Live snapshot: mode, armed, battery, GPS fix type, position. Use before any advice or action.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "run_preflight_checklist",
        "description": "Run the pre-arm checklist against live telemetry. Returns arm_allowed plus human checklist items.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "plan_spray_mission",
        "description": ("Convert a weed-detections GeoJSON into a spray mission file. "
                        "Safety-checked against zone bounds. Returns a file to REVIEW in QGC - never auto-uploads."),
        "parameters": {
            "type": "object",
            "properties": {
                "geojson_path": {"type": "string"},
                "terrain_tiff": {"type": ["string", "null"]},
                "tank_litres": {"type": "number"},
            },
            "required": ["geojson_path"],
        },
    }},
    {"type": "function", "function": {
        "name": "request_spray_enable",
        "description": "Check whether spray is currently permitted (armed, RTK fixed, in-zone, altitude). Advisory only.",
        "parameters": {"type": "object", "properties": {}},
    }},
]

_DISPATCH = {
    "get_aircraft_status": get_aircraft_status,
    "run_preflight_checklist": run_preflight_checklist,
    "plan_spray_mission": plan_spray_mission,
    "request_spray_enable": request_spray_enable,
}
_VALIDATORS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}


def execute_tool(name, args):
    if name not in _DISPATCH:
        return {"error": f"unknown tool: {name}"}
    try:
        jsonschema.validate(args, _VALIDATORS[name])
    except jsonschema.ValidationError as e:
        return {"error": f"invalid arguments: {e.message}"}
    try:
        return _DISPATCH[name](**args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
