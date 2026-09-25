"""MAVLink node: telemetry recorder + status + arm/mode + RTCM passthrough.

Talks to the aircraft (SITL now, Pixhawk 4 / Cube later) through MAVProxy's
second output: udp:127.0.0.1:14551.

IMPORTANT (INTEGRATION.md): this module does NOT send MAV_CMD_DO_SPRAYER.
Only mavlink_hub.MavlinkHub may write spray commands. Use MavNode for
telemetry, arm/mode helpers, QGC .plan writing, and RTCM injection to the F9P.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Optional

from pymavlink import mavutil


class MavNode:
    def __init__(self, connection="udp:127.0.0.1:14551", record_dir="recordings"):
        self.conn = mavutil.mavlink_connection(connection)
        self.record_dir = record_dir
        os.makedirs(record_dir, exist_ok=True)
        self.state = {
            "messages": [],
            "rc_link_ok": None,
            "video_trusted": None,
            "live_id_enabled": None,
            "heartbeat_age_s": None,
        }
        self._stop = threading.Event()
        self._thread = None
        self._csv = None
        self._last_hb = None

    def start(self):
        print("Waiting for heartbeat...")
        self.conn.wait_heartbeat(timeout=30)
        print(f"Heartbeat from system {self.conn.target_system}")
        self._last_hb = time.time()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._csv = open(os.path.join(self.record_dir, f"telemetry_{stamp}.csv"),
                         "w", newline="")
        self._csv.write("timestamp,msg_type,lat,lon,alt_m,heading_deg,battery_pct\n")
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._csv:
            self._csv.close()

    def _reader(self):
        while not self._stop.is_set():
            msg = self.conn.recv_match(blocking=True, timeout=1)
            if msg is None:
                if self._last_hb:
                    self.state["heartbeat_age_s"] = time.time() - self._last_hb
                continue
            t = msg.get_type()
            if t == "GLOBAL_POSITION_INT":
                self.state.update(lat=msg.lat / 1e7, lon=msg.lon / 1e7,
                                  alt=msg.alt / 1e3, rel_alt=msg.relative_alt / 1e3,
                                  heading=msg.hdg / 100.0)
            elif t == "SYS_STATUS":
                self.state.update(battery=msg.battery_remaining,
                                  voltage=msg.voltage_battery / 1000.0)
            elif t == "GPS_RAW_INT":
                self.state["fix_type"] = msg.fix_type
            elif t == "HEARTBEAT":
                self._last_hb = time.time()
                self.state["heartbeat_age_s"] = 0.0
                self.state["mode"] = mavutil.mode_string_v10(msg)
                self.state["armed"] = bool(msg.base_mode
                                           & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            elif t == "RC_CHANNELS" or t == "RC_CHANNELS_RAW":
                # Presence of RC channel frames => ELRS/RC path alive
                self.state["rc_link_ok"] = True
                rssi = getattr(msg, "rssi", None)
                if rssi is not None:
                    self.state["rc_rssi"] = rssi
                    if rssi == 0 or rssi == 255:
                        self.state["rc_link_ok"] = False
            elif t == "STATUSTEXT":
                self.state["messages"].append(msg.text)
            elif t == "CAMERA_FEEDBACK":
                self.state["last_camera"] = {"lat": msg.lat / 1e7,
                                             "lon": msg.lon / 1e7,
                                             "alt": msg.alt_msl}
            if self._csv and t in ("GLOBAL_POSITION_INT", "SYS_STATUS"):
                self._csv.write(
                    f"{time.time()},{t},{self.state.get('lat','')},"
                    f"{self.state.get('lon','')},{self.state.get('rel_alt','')},"
                    f"{self.state.get('heading','')},{self.state.get('battery','')}\n")
                self._csv.flush()

    def get_status(self):
        s = dict(self.state)
        s["timestamp"] = time.time()
        if self._last_hb:
            s["heartbeat_age_s"] = time.time() - self._last_hb
        return s

    def set_operator_flags(self, *, video_trusted=None, live_id_enabled=None,
                           in_geofence=None, failsafe=None):
        """GS pipeline pushes trust / enable flags into status for SafetyGate."""
        if video_trusted is not None:
            self.state["video_trusted"] = bool(video_trusted)
        if live_id_enabled is not None:
            self.state["live_id_enabled"] = bool(live_id_enabled)
        if in_geofence is not None:
            self.state["in_geofence"] = bool(in_geofence)
        if failsafe is not None:
            self.state["failsafe"] = bool(failsafe)

    # ---------- arm / mode (not spray) ----------
    def arm(self, arm: bool = True) -> dict:
        self.conn.mav.command_long_send(
            self.conn.target_system, self.conn.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0, 1.0 if arm else 0.0, 0, 0, 0, 0, 0, 0,
        )
        return {"requested": "arm" if arm else "disarm"}

    def set_mode(self, mode: str) -> dict:
        mode_map = self.conn.mode_mapping()
        if mode_map is None or mode not in mode_map:
            return {"error": f"unknown mode {mode}", "available": list((mode_map or {}).keys())}
        mode_id = mode_map[mode]
        self.conn.set_mode(mode_id)
        return {"requested_mode": mode, "mode_id": mode_id}

    # ---------- RTCM passthrough (F9P base -> vehicle GPS) ----------
    def inject_rtcm(self, rtcm_bytes: bytes) -> dict:
        """Forward RTCM3 bytes as GPS_RTCM_DATA fragments (MAVLink2)."""
        if not rtcm_bytes:
            return {"error": "empty RTCM"}
        # MAVLink GPS_RTCM_DATA payload is 180 bytes; flags encode fragment
        max_chunk = 180
        n = len(rtcm_bytes)
        seq = getattr(self, "_rtcm_seq", 0) & 0x1F
        self._rtcm_seq = seq + 1
        fragments = (n + max_chunk - 1) // max_chunk
        for i in range(fragments):
            chunk = rtcm_bytes[i * max_chunk:(i + 1) * max_chunk]
            buf = chunk + bytes(max_chunk - len(chunk))
            # flags: bit0 fragmented, bits1-2 frag id, bits3-7 sequence
            flags = 0
            if fragments > 1:
                flags = 1 | ((i & 0x3) << 1) | ((seq & 0x1F) << 3)
            self.conn.mav.gps_rtcm_data_send(flags, len(chunk), list(buf))
        return {"injected_bytes": n, "fragments": fragments, "seq": seq}

    # ---------- mission output (unchanged QGC path) ----------
    def save_qgc_plan(self, waypoints, filename, default_alt=15.0):
        """Write a QGroundControl .plan file (import via QGC -> File -> Open).
        waypoints: list of (lat, lon, alt_m). Draft format -- verify in QGC
        before first real upload."""
        items = []
        for i, (lat, lon, alt) in enumerate(waypoints):
            items.append({
                "AMSLAltAboveTerrain": None, "Altitude": alt,
                "AltitudeMode": 1, "autoContinue": True,
                "command": 16,
                "doJumpId": i + 1, "frame": 3, "params": [0, 0, 0, 0, lat, lon, alt],
                "type": "SimpleItem",
            })
        plan = {
            "fileType": "Plan", "version": 1,
            "groundStation": "drone_stack_middleware",
            "mission": {
                "cruiseSpeed": 5, "firmwareType": 3, "hoverSpeed": 2,
                "items": items,
                "plannedHomePosition": list(waypoints[0][:2]) + [default_alt],
            },
        }
        with open(filename, "w") as f:
            json.dump(plan, f, indent=2)
        return filename


if __name__ == "__main__":
    node = MavNode()
    node.start()
    try:
        while True:
            time.sleep(2)
            print(json.dumps(node.get_status(), indent=2, default=str))
    except KeyboardInterrupt:
        node.stop()
