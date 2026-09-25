"""Deterministic safety interlocks. No LLM, no randomness, no exceptions.

The agent layer may *request* actions; this gate decides. Anything that
actuates (arm, spray, upload) passes through here first.
"""
from dataclasses import dataclass, field


@dataclass
class Verdict:
    allowed: bool
    reasons: list = field(default_factory=list)


class SafetyGate:
    def can_arm(self, status: dict) -> Verdict:
        reasons = []
        if "mode" not in status and "armed" not in status:
            reasons.append("no telemetry: link down")
        if status.get("battery", 101) < 30:
            reasons.append(f"battery {status.get('battery')}% < 30%")
        fix = status.get("fix_type", 0)
        if fix < 3:
            reasons.append(f"GPS fix type {fix} < 3 (3D)")
        if status.get("mode", "").upper() in ("AUTO", "RTL", "LAND"):
            reasons.append(f"already in {status.get('mode')}")
        return Verdict(not reasons, reasons)

    def can_spray(self, status: dict, in_geofence: bool = True) -> Verdict:
        reasons = []
        if not status.get("armed"):
            reasons.append("aircraft not armed")
        fix = status.get("fix_type", 0)
        if fix < 6:
            reasons.append(f"GPS fix type {fix}: RTK fixed (6) required to spray")
        if status.get("rel_alt", 999) > 30:
            reasons.append(f"altitude {status.get('rel_alt')}m > 30m spray ceiling")
        if not in_geofence:
            reasons.append("position outside spray zone geofence")
        if status.get("battery", 101) < 20:
            reasons.append(f"battery {status.get('battery')}% < 20% RTL reserve")
        return Verdict(not reasons, reasons)

    def check_mission(self, waypoints: list, zone_bounds: tuple) -> Verdict:
        """waypoints: (lat, lon, alt) list; zone_bounds: (minlat, minlon, maxlat, maxlon)."""
        reasons = []
        minlat, minlon, maxlat, maxlon = zone_bounds
        for i, (lat, lon, alt) in enumerate(waypoints):
            if not (minlat <= lat <= maxlat and minlon <= lon <= maxlon):
                reasons.append(f"wp{i}: ({lat:.5f},{lon:.5f}) outside zone")
            if alt > 50:
                reasons.append(f"wp{i}: alt {alt}m > 50m hard ceiling")
        if len(waypoints) > 500:
            reasons.append("mission too long (>500 items)")
        return Verdict(not reasons, reasons)
