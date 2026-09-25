"""Sole MAVLink COMMAND_LONG writer for spray (INTEGRATION.md).

Only MavlinkHub may send MAV_CMD_DO_SPRAYER. Live ID, voice, and the Ollama
agent must request spray through this arbiter — never via MavNode directly.

Fail closed: SafetyGate denial => pump off (DO_SPRAYER param1=0).
"""
from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

from pymavlink import mavutil

from safety_gate import SafetyGate

# MAV_CMD_DO_SPRAYER = 216
MAV_CMD_DO_SPRAYER = 216


class MavlinkHub:
    """Single spray arbiter. One process should own one Hub per aircraft link."""

    def __init__(self, mav_node, gate: Optional[SafetyGate] = None):
        self.node = mav_node
        self.gate = gate or SafetyGate()
        self._lock = threading.Lock()
        self._pump_on = False
        self._last_cmd = None

    def _send_sprayer(self, start: bool) -> None:
        conn = self.node.conn
        conn.mav.command_long_send(
            conn.target_system,
            conn.target_component,
            MAV_CMD_DO_SPRAYER,
            0,
            1.0 if start else 0.0,  # param1: 1 start, 0 stop
            0, 0, 0, 0, 0, 0,
        )
        self._pump_on = start
        self._last_cmd = {"sprayer": "start" if start else "stop", "t": time.time()}

    def request_spray_pulse(
        self,
        *,
        conf: float,
        georeg: float,
        dose_ml: float,
        zone_bounds: Optional[Tuple[float, float, float, float]] = None,
        video_trusted: bool = True,
        live_id_enabled: bool = True,
        on_time_s: float = 0.1,
    ) -> dict:
        """Arbiter entry: gate check then brief DO_SPRAYER start/stop pulse."""
        with self._lock:
            status = self.node.get_status()
            verdict = self.gate.can_spray(
                status,
                zone_bounds=zone_bounds,
                conf=conf,
                georeg=georeg,
                video_trusted=video_trusted,
                live_id_enabled=live_id_enabled,
            )
            if not verdict.allowed:
                self._send_sprayer(False)
                return {
                    "ok": False,
                    "pump": "off",
                    "blockers": verdict.reasons,
                    "dose_ml": dose_ml,
                }
            # Clamp on-time to LIVE-PIPELINE bench window
            t_on = max(0.04, min(0.40, float(on_time_s)))
            self._send_sprayer(True)
            time.sleep(t_on)
            self._send_sprayer(False)
            return {
                "ok": True,
                "pump": "pulsed",
                "on_time_s": t_on,
                "dose_ml": dose_ml,
                "conf": conf,
                "georeg": georeg,
            }

    def force_pump_off(self, reason: str = "fail-closed") -> dict:
        """Always allowed: stop sprayer. Call on video loss / gate fault."""
        with self._lock:
            self._send_sprayer(False)
            return {"ok": True, "pump": "off", "reason": reason}

    def spray_status(self) -> dict:
        return {
            "pump_on": self._pump_on,
            "last_cmd": self._last_cmd,
            "note": "Only MavlinkHub sends MAV_CMD_DO_SPRAYER",
        }
