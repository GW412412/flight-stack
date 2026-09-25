"""Agent tool registry: schemas + validation + dispatch.

Design rules:
  - Every tool is validated against its JSON schema before execution.
  - Tools return plain dicts; the agent never sees raw exceptions.
  - Actuation that sprays goes ONLY through mavlink_hub (INTEGRATION).
  - LLM / Ollama is advisory — never RC authority, never bypasses SafetyGate.
"""
from __future__ import annotations

import time
import jsonschema

from mav_node import MavNode
from mavlink_hub import MavlinkHub
from safety_gate import SafetyGate
from spray_mission import generate_spray_plan, dose_ml, CONF_MIN, TANK_LITRES_DEFAULT

_node = MavNode()
_gate = SafetyGate()
_hub = MavlinkHub(_node, _gate)
_started = False

# In-memory stubs until live_id / voice modules land on GS
_LIVE = {
    "enabled": False,
    "last_detections": [],
    "approach_scan": {"armed": False, "wp_id": None, "active_list": []},
}
_VOICE = {"last_heard": None, "pending_confirm": None, "authority": "advisory_only"}


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
                   ("mode", "armed", "battery", "fix_type", "lat", "lon", "rel_alt",
                    "rc_link_ok", "video_trusted", "live_id_enabled")},
        "checklist": [
            "props clear" if not status.get("armed") else "PROPS AREA - ARMED",
            "geofence loaded (verify in QGC)",
            "spray tank: WATER ONLY for testing — 0.5 L concentrate tank when chemical",
            "RTK corrections streaming" if status.get("fix_type", 0) >= 5
                else "WARNING: no RTK (spray will be blocked)",
            "ELRS RC link" if status.get("rc_link_ok") else "verify ELRS RC link",
        ],
    }


def plan_spray_mission(geojson_path, terrain_tiff=None, tank_litres=TANK_LITRES_DEFAULT):
    """Generate + safety-check a spray mission. Returns plan path; never uploads."""
    _ensure_started()
    waypoints, bounds, meta = generate_spray_plan(
        geojson_path, terrain_tiff, tank_litres, conf_min=CONF_MIN)
    verdict = _gate.check_mission(waypoints, bounds)
    if not verdict.allowed:
        return {"error": "mission failed safety check", "reasons": verdict.reasons}
    path = _node.save_qgc_plan(waypoints, f"spray_plan_{int(time.time())}.plan")
    return {
        "mission_file": path,
        "waypoint_count": len(waypoints),
        "zone_bounds": bounds,
        "dose_meta": meta,
        "review": "Import into QGroundControl and verify before upload.",
    }


def request_spray_enable():
    """DEPRECATED / advisory only.

    Does not pulse the pump. Use live pipeline -> MavlinkHub.request_spray_pulse.
    Kept so older agent prompts do not crash.
    """
    _ensure_started()
    status = _node.get_status()
    v = _gate.can_spray(
        status,
        conf=CONF_MIN,
        georeg=0.70,
        video_trusted=status.get("video_trusted"),
        live_id_enabled=status.get("live_id_enabled"),
        in_geofence=status.get("in_geofence"),
    )
    return {
        "deprecated": True,
        "advisory_only": True,
        "spray_allowed": v.allowed,
        "blockers": v.reasons,
        "note": (
            "DEPRECATED. This tool never sends MAV_CMD_DO_SPRAYER. "
            "Only mavlink_hub may write spray commands. "
            "Physical / RC inhibit still applies. Prefer live_id_status + hub."
        ),
    }


def live_id_status():
    """Stub: live YOLO / georeg pipeline status (GS)."""
    _ensure_started()
    st = _node.get_status()
    return {
        "stub": True,
        "live_id_enabled": st.get("live_id_enabled", _LIVE["enabled"]),
        "video_trusted": st.get("video_trusted"),
        "conf_gate": CONF_MIN,
        "georeg_gate": 0.70,
        "last_detections": _LIVE["last_detections"],
        "note": "Wire to GS YOLOv8n-seg ONNX per LIVE-PIPELINE.md",
    }


def compute_dose(area_cm2: float):
    """Stub/helper: locked dose formula for a canopy area."""
    v = dose_ml(float(area_cm2))
    return {
        "area_cm2": area_cm2,
        "dose_ml": v,
        "skipped": v is None,
        "formula": "clamp(0.25 + 1.25*A_cm2, 0.1, 2.5) mL; skip if A < 0.5",
        "tank_litres": TANK_LITRES_DEFAULT,
    }


def approach_scan_status(wp_id=None):
    """Stub: approach-scan mini search-and-destroy state."""
    a = dict(_LIVE["approach_scan"])
    if wp_id is not None:
        a["wp_id"] = wp_id
    return {
        "stub": True,
        "approach_scan": a,
        "locks": {"conf": CONF_MIN, "georeg": 0.70, "max_inserts_per_wp": 3},
        "note": "See LIVE-PIPELINE §4.6; path adjust only via mavlink_hub",
    }


def voice_status():
    """Stub: voice layer status (VOICE-CONTROL). Advisory; ELRS remains hard authority."""
    return {
        "stub": True,
        "voice": _VOICE,
        "authority": "ELRS is hard flight authority; voice is confirm-gated convenience",
        "note": "Ollama agent is separate and also advisory-only",
    }


def hub_spray_status():
    _ensure_started()
    return _hub.spray_status()


TOOLS = [
    {"type": "function", "function": {
        "name": "get_aircraft_status",
        "description": "Live snapshot: mode, armed, battery, GPS fix, RC, video flags, position.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "run_preflight_checklist",
        "description": "Pre-arm checklist against live telemetry + SafetyGate.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "plan_spray_mission",
        "description": (
            "GeoJSON -> QGC .plan with conf>=0.50 filter and locked dose formula. "
            "Never auto-uploads. Tank default 0.5 L concentrate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "geojson_path": {"type": "string"},
                "terrain_tiff": {"type": ["string", "null"]},
                "tank_litres": {"type": "number", "default": 0.5},
            },
            "required": ["geojson_path"],
        },
    }},
    {"type": "function", "function": {
        "name": "request_spray_enable",
        "description": "DEPRECATED advisory gate check only. Does not spray. Prefer hub + live ID.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "live_id_status",
        "description": "Stub: live weed-ID pipeline status (conf/georeg gates).",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "compute_dose",
        "description": "Locked dose V=clamp(0.25+1.25*A_cm2,0.1,2.5) mL from canopy area cm2.",
        "parameters": {
            "type": "object",
            "properties": {"area_cm2": {"type": "number"}},
            "required": ["area_cm2"],
        },
    }},
    {"type": "function", "function": {
        "name": "approach_scan_status",
        "description": "Stub: approach-scan state for a mapped waypoint.",
        "parameters": {
            "type": "object",
            "properties": {"wp_id": {"type": ["string", "null"]}},
        },
    }},
    {"type": "function", "function": {
        "name": "voice_status",
        "description": "Stub: voice-control layer status. ELRS remains hard authority.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "hub_spray_status",
        "description": "MavlinkHub pump/arbiter status (sole DO_SPRAYER writer).",
        "parameters": {"type": "object", "properties": {}},
    }},
]

_DISPATCH = {
    "get_aircraft_status": get_aircraft_status,
    "run_preflight_checklist": run_preflight_checklist,
    "plan_spray_mission": plan_spray_mission,
    "request_spray_enable": request_spray_enable,
    "live_id_status": live_id_status,
    "compute_dose": compute_dose,
    "approach_scan_status": approach_scan_status,
    "voice_status": voice_status,
    "hub_spray_status": hub_spray_status,
}
_VALIDATORS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}


def execute_tool(name, args):
    if name not in _DISPATCH:
        return {"error": f"unknown tool: {name}"}
    try:
        jsonschema.validate(args or {}, _VALIDATORS[name])
    except jsonschema.ValidationError as e:
        return {"error": f"invalid arguments: {e.message}"}
    try:
        return _DISPATCH[name](**(args or {}))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
