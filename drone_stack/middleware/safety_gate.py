"""Deterministic safety interlocks. No LLM, no randomness.

Aligned to LIVE-PIPELINE + INTEGRATION:
  conf >= 0.50, georeg g >= 0.70, RC link present, video trusted,
  fail-closed pump off on any fault. Geofence uses real bounds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

CONF_MIN = 0.50
GEOREG_MIN = 0.70


@dataclass
class Verdict:
    allowed: bool
    reasons: list = field(default_factory=list)


def _in_bounds(lat: float, lon: float, zone_bounds: Tuple[float, float, float, float]) -> bool:
    minlat, minlon, maxlat, maxlon = zone_bounds
    return minlat <= lat <= maxlat and minlon <= lon <= maxlon


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
        if status.get("rc_link_ok") is False:
            reasons.append("RC / ELRS link not present")
        return Verdict(not reasons, reasons)

    def can_spray(
        self,
        status: dict,
        *,
        zone_bounds: Optional[Tuple[float, float, float, float]] = None,
        in_geofence: Optional[bool] = None,
        conf: Optional[float] = None,
        georeg: Optional[float] = None,
        video_trusted: Optional[bool] = None,
        live_id_enabled: Optional[bool] = None,
    ) -> Verdict:
        """Live / pulse spray gate. Fail closed: any unmet check blocks + implies pump off."""
        reasons = []
        if not status.get("armed"):
            reasons.append("aircraft not armed")
        fix = status.get("fix_type", 0)
        if fix < 6:
            reasons.append(f"GPS fix type {fix}: RTK fixed (6) required to spray")
        if status.get("rel_alt", 999) > 30:
            reasons.append(f"altitude {status.get('rel_alt')}m > 30m spray ceiling")
        if status.get("battery", 101) < 20:
            reasons.append(f"battery {status.get('battery')}% < 20% RTL reserve")

        # RC link (ELRS hard authority path must be healthy) — fail closed if unknown
        if status.get("rc_link_ok") is not True:
            reasons.append("RC / ELRS link not present")
        if status.get("heartbeat_age_s") is not None and status.get("heartbeat_age_s") > 2.0:
            reasons.append("telemetry heartbeat stale — fail closed")

        # Video link trusted (live path). Explicit False or missing when required.
        vt = video_trusted if video_trusted is not None else status.get("video_trusted")
        if vt is False:
            reasons.append("video link not trusted — pump must stay off")

        # Live ID operator enable
        lie = live_id_enabled if live_id_enabled is not None else status.get("live_id_enabled")
        if lie is False:
            reasons.append("live ID not operator-enabled")

        # Detection gates when a detection is under review
        if conf is not None and conf < CONF_MIN:
            reasons.append(f"conf {conf} < {CONF_MIN}")
        if georeg is not None and georeg < GEOREG_MIN:
            reasons.append(f"georeg {georeg} < {GEOREG_MIN}")

        # Geofence: prefer explicit zone_bounds + aircraft lat/lon
        if zone_bounds is not None:
            lat, lon = status.get("lat"), status.get("lon")
            if lat is None or lon is None:
                reasons.append("no position for geofence check")
            elif not _in_bounds(float(lat), float(lon), zone_bounds):
                reasons.append(
                    f"position ({lat},{lon}) outside spray zone geofence"
                )
        elif in_geofence is False:
            reasons.append("position outside spray zone geofence")
        elif in_geofence is None and zone_bounds is None:
            # Do not default-true: require caller to supply fence or explicit True
            if status.get("in_geofence") is True:
                pass
            elif status.get("in_geofence") is False:
                reasons.append("position outside spray zone geofence")
            else:
                reasons.append("geofence not verified (pass zone_bounds or in_geofence)")

        # Any fault flag on status
        if status.get("failsafe") or status.get("pump_fault"):
            reasons.append("failsafe or pump fault — fail closed, pump off")

        return Verdict(not reasons, reasons)

    def pump_must_be_off(self, status: dict, **kwargs) -> Verdict:
        """Fail-closed helper: if can_spray is denied, pump must be off."""
        v = self.can_spray(status, **kwargs)
        if not v.allowed:
            return Verdict(True, ["pump off: " + "; ".join(v.reasons)])
        return Verdict(False, ["spray conditions currently allow pump under hub arbiter"])

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
