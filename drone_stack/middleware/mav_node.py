"""MAVLink node: telemetry recorder + status provider for the agent tool layer.

Talks to the aircraft (SITL now, Pixhawk later) through MAVProxy's second
output port: udp:127.0.0.1:14551. Records every flight to CSV for the AI
pipeline and writes QGroundControl .plan mission files.
"""
import csv
import json
import os
import threading
import time
from datetime import datetime

from pymavlink import mavutil


class MavNode:
    def __init__(self, connection="udp:127.0.0.1:14551", record_dir="recordings"):
        self.conn = mavutil.mavlink_connection(connection)
        self.record_dir = record_dir
        os.makedirs(record_dir, exist_ok=True)
        self.state = {"messages": []}
        self._stop = threading.Event()
        self._thread = None
        self._csv = None

    def start(self):
        print("Waiting for heartbeat...")
        self.conn.wait_heartbeat(timeout=30)
        print(f"Heartbeat from system {self.conn.target_system}")
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
                self.state["mode"] = mavutil.mode_string_v10(msg)
                self.state["armed"] = bool(msg.base_mode
                                           & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
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
        return s

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
