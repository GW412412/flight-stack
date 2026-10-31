#!/usr/bin/env python3
"""fake_vehicle.py - a tiny pretend ArduCopter for tests and demos without SITL.

It is NOT a simulator. It speaks just enough MAVLink for the MCP stack:

  * HEARTBEAT (quadrotor / ArduPilot, copter custom modes), 1 Hz
  * GLOBAL_POSITION_INT, SYS_STATUS (incl. RC receiver health), GPS_RAW_INT,
    EKF_STATUS_REPORT, VFR_HUD, ATTITUDE, EXTENDED_SYS_STATE - 10 Hz
  * COMMAND_LONG: ARM/DISARM, DO_SET_MODE, NAV_TAKEOFF, DO_CHANGE_SPEED,
    REQUEST_MESSAGE(HOME_POSITION) -> COMMAND_ACK
  * SET_POSITION_TARGET_GLOBAL_INT in GUIDED -> flies there in a straight line
  * PARAM_REQUEST_READ / PARAM_SET -> PARAM_VALUE (a handful of params)
  * Mission upload and download (MISSION_COUNT / REQUEST_INT / ITEM_INT / ACK)
  * RTL / LAND descend and disarm on the ground

It talks to a pymavlink `udpin:` listener (like MAVProxy's --out does), so
the MCP server can be pointed at it unchanged:

    python fake_vehicle.py --out 127.0.0.1:14560     # instead of SITL+MAVProxy
    python mcp_server.py --config ../../config/llm_stack.sitl.toml

`simulate_pilot_mode(mode)` lets a test pretend the RC pilot flicked the mode
switch (to exercise pilot-override detection).
"""
from __future__ import annotations

import argparse
import math
import os
import threading
import time
from typing import Dict, List, Optional

# ArduPilot speaks MAVLink 2. Without this pymavlink loads the MAVLink 1
# dialect until it happens to see a v2 packet, and v2-only fields such as
# mission_type / COMMAND_ACK.target_system would be missing. Must be set
# before pymavlink is imported.
os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil  # noqa: E402

M = mavutil.mavlink
COPTER_MODES = {"STABILIZE": 0, "ALT_HOLD": 2, "AUTO": 3, "GUIDED": 4, "LOITER": 5,
                "RTL": 6, "LAND": 9, "POSHOLD": 16, "BRAKE": 17, "SMART_RTL": 21}
EKF_OK = 1 | 2 | 4 | 8 | 16 | 32


class FakeVehicle:
    def __init__(self, out: str = "127.0.0.1:14560", home=(-35.363261, 149.165230, 584.0),
                 sysid: int = 1, rate_hz: float = 10.0):
        self.conn = mavutil.mavlink_connection(f"udpout:{out}", source_system=sysid,
                                               source_component=1)
        self.home = home
        self.lat, self.lon, self.alt = home[0], home[1], 0.0   # alt above home
        self.mode = "STABILIZE"
        self.armed = False
        self.target: Optional[tuple] = None   # (lat, lon, alt)
        self.speed = 5.0
        self.battery = 95
        self.params: Dict[str, float] = {"WP_SPD": 10.0, "RTL_ALT_M": 15.0,
                                         "LAND_SPD_MS": 0.5, "FS_GCS_ENABLE": 1.0,
                                         "SYSID_MYGCS": 255.0}
        self.mission: List[tuple] = []
        self._upload_expect = 0
        self._upload_items: List[tuple] = []
        self.rate_hz = rate_hz
        self.commands_seen: List[str] = []    # for test assertions
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads: List[threading.Thread] = []

    # ------------------------------------------------------------ lifecycle
    def start(self) -> "FakeVehicle":
        for fn in (self._tx_loop, self._rx_loop):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        self.conn.close()

    def simulate_pilot_mode(self, mode: str):
        """Pretend the RC mode switch moved (no MAVLink command involved)."""
        with self._lock:
            self.mode = mode

    # ------------------------------------------------------------ physics (ha)
    def _step(self, dt: float):
        with self._lock:
            if not self.armed:
                return
            if self.mode == "GUIDED" and self.target:
                tlat, tlon, talt = self.target
                dn = (tlat - self.lat) * 111320.0
                de = (tlon - self.lon) * 111320.0 * math.cos(math.radians(self.lat))
                d = math.hypot(dn, de)
                step = min(d, self.speed * dt)
                if d > 1e-3:
                    self.lat += (dn / d) * step / 111320.0
                    self.lon += (de / d) * step / (111320.0 * math.cos(math.radians(self.lat)))
                dz = talt - self.alt
                self.alt += max(-2.0 * dt, min(2.0 * dt, dz))
            elif self.mode in ("LAND", "RTL"):
                if self.mode == "RTL":
                    self.lat, self.lon = self.home[0], self.home[1]
                self.alt = max(0.0, self.alt - 3.0 * dt)
                if self.alt <= 0.01:
                    self.armed = False

    # ------------------------------------------------------------ transmit
    def _tx_loop(self):
        period, last_hb = 1.0 / self.rate_hz, 0.0
        while not self._stop.is_set():
            self._step(period)
            now = time.time()
            m = self.conn.mav
            try:
                if now - last_hb >= 1.0:
                    base = M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | (
                        M.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0)
                    m.heartbeat_send(M.MAV_TYPE_QUADROTOR, M.MAV_AUTOPILOT_ARDUPILOTMEGA,
                                     base, COPTER_MODES[self.mode], M.MAV_STATE_ACTIVE)
                    last_hb = now
                boot_ms = int((now % 1e6) * 1000) & 0xFFFFFFFF
                m.global_position_int_send(boot_ms, int(self.lat * 1e7), int(self.lon * 1e7),
                                           int((self.home[2] + self.alt) * 1000),
                                           int(self.alt * 1000), 0, 0, 0, 0)
                rc = M.MAV_SYS_STATUS_SENSOR_RC_RECEIVER
                m.sys_status_send(rc, rc, rc, 100, 12600, 1000, self.battery, 0, 0, 0, 0, 0, 0)
                m.gps_raw_int_send(now_us(), 3, int(self.lat * 1e7), int(self.lon * 1e7),
                                   int(self.home[2] * 1000), 80, 100, 0, 0, 12)
                m.ekf_status_report_send(EKF_OK, 0.1, 0.1, 0.1, 0.1, 0.1)
                m.vfr_hud_send(0, 0, 0, 30, self.alt, 0)
                m.attitude_send(boot_ms, 0, 0, 0, 0, 0, 0)
                m.extended_sys_state_send(
                    0, M.MAV_LANDED_STATE_ON_GROUND if self.alt < 0.1
                    else M.MAV_LANDED_STATE_IN_AIR)
            except OSError:
                pass       # listener not up yet
            self._stop.wait(period)

    # ------------------------------------------------------------ receive
    def _rx_loop(self):
        while not self._stop.is_set():
            try:
                msg = self.conn.recv_match(blocking=True, timeout=0.2)
            except OSError:
                continue
            if msg is None or msg.get_type() == "BAD_DATA":
                continue
            try:
                self._handle(msg)
            except Exception as exc:      # keep the fake alive in tests
                print("fake_vehicle handler error:", exc)

    def _ack(self, cmd: int, result: int, msg):
        self.conn.mav.command_ack_send(cmd, result, progress=0, result_param2=0,
                                       target_system=msg.get_srcSystem(),
                                       target_component=msg.get_srcComponent())

    def _handle(self, msg):
        t = msg.get_type()
        m = self.conn.mav
        if t == "COMMAND_LONG":
            self.commands_seen.append(M.enums["MAV_CMD"][msg.command].name
                                      if msg.command in M.enums["MAV_CMD"] else str(msg.command))
            with self._lock:
                res = self._command(msg)
            self._ack(msg.command, res, msg)
        elif t == "SET_POSITION_TARGET_GLOBAL_INT":
            self.commands_seen.append("SET_POSITION_TARGET_GLOBAL_INT")
            with self._lock:
                if self.mode == "GUIDED" and self.armed:
                    self.target = (msg.lat_int / 1e7, msg.lon_int / 1e7, msg.alt)
        elif t == "PARAM_REQUEST_READ":
            name = msg.param_id
            if name in self.params:
                m.param_value_send(name.encode(), self.params[name], M.MAV_PARAM_TYPE_REAL32,
                                   len(self.params), list(self.params).index(name))
        elif t == "PARAM_SET":
            self.commands_seen.append(f"PARAM_SET {msg.param_id}")
            if msg.param_id in self.params:
                self.params[msg.param_id] = msg.param_value
                m.param_value_send(msg.param_id.encode(), msg.param_value,
                                   M.MAV_PARAM_TYPE_REAL32, len(self.params),
                                   list(self.params).index(msg.param_id))
        elif t == "MISSION_COUNT":
            self._upload_expect, self._upload_items = msg.count, []
            m.mission_request_int_send(msg.get_srcSystem(), msg.get_srcComponent(), 0,
                                       M.MAV_MISSION_TYPE_MISSION)
        elif t == "MISSION_ITEM_INT":
            self._upload_items.append((msg.seq, msg.command, msg.x, msg.y, msg.z, msg.frame))
            nxt = msg.seq + 1
            if nxt < self._upload_expect:
                m.mission_request_int_send(msg.get_srcSystem(), msg.get_srcComponent(), nxt,
                                           M.MAV_MISSION_TYPE_MISSION)
            else:
                self.mission = list(self._upload_items)
                m.mission_ack_send(msg.get_srcSystem(), msg.get_srcComponent(),
                                   M.MAV_MISSION_ACCEPTED, M.MAV_MISSION_TYPE_MISSION)
        elif t == "MISSION_REQUEST_LIST":
            m.mission_count_send(msg.get_srcSystem(), msg.get_srcComponent(),
                                 len(self.mission), M.MAV_MISSION_TYPE_MISSION)
        elif t in ("MISSION_REQUEST_INT", "MISSION_REQUEST"):
            if msg.seq < len(self.mission):
                seq, cmd, x, y, z, frame = self.mission[msg.seq]
                m.mission_item_int_send(msg.get_srcSystem(), msg.get_srcComponent(), seq,
                                        frame, cmd, 0, 1, 0, 0, 0, 0, x, y, z,
                                        M.MAV_MISSION_TYPE_MISSION)

    def _command(self, msg) -> int:
        """Apply one COMMAND_LONG; return a MAV_RESULT. Caller holds the lock."""
        c = msg.command
        if c == M.MAV_CMD_COMPONENT_ARM_DISARM:
            if msg.param1 == 1:
                if self.mode not in ("GUIDED", "LOITER", "STABILIZE", "ALT_HOLD", "POSHOLD"):
                    return M.MAV_RESULT_FAILED
                self.armed = True
            else:
                if self.alt > 0.5 and msg.param2 != 21196:
                    return M.MAV_RESULT_FAILED
                self.armed = False
            return M.MAV_RESULT_ACCEPTED
        if c == M.MAV_CMD_DO_SET_MODE:
            num = int(msg.param2)
            names = {v: k for k, v in COPTER_MODES.items()}
            if num not in names:
                return M.MAV_RESULT_UNSUPPORTED
            self.mode = names[num]
            if self.mode != "GUIDED":
                self.target = None
            return M.MAV_RESULT_ACCEPTED
        if c == M.MAV_CMD_NAV_TAKEOFF:
            if not self.armed or self.mode != "GUIDED":
                return M.MAV_RESULT_FAILED
            self.target = (self.lat, self.lon, float(msg.param7))
            return M.MAV_RESULT_ACCEPTED
        if c == M.MAV_CMD_DO_CHANGE_SPEED:
            self.speed = float(msg.param2)
            return M.MAV_RESULT_ACCEPTED
        if c == M.MAV_CMD_REQUEST_MESSAGE:
            if int(msg.param1) == M.MAVLINK_MSG_ID_HOME_POSITION:
                self.conn.mav.home_position_send(int(self.home[0] * 1e7),
                                                 int(self.home[1] * 1e7),
                                                 int(self.home[2] * 1000), 0, 0, 0,
                                                 [1, 0, 0, 0], 0, 0, 0)
                return M.MAV_RESULT_ACCEPTED
            return M.MAV_RESULT_UNSUPPORTED
        return M.MAV_RESULT_UNSUPPORTED


def now_us() -> int:
    return int(time.time() * 1e6)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="127.0.0.1:14560",
                    help="where the MCP server (udpin) listens")
    a = ap.parse_args()
    fv = FakeVehicle(a.out).start()
    print(f"fake vehicle sending to udp {a.out} (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
            print(f"mode {fv.mode} armed {fv.armed} alt {fv.alt:.1f} "
                  f"pos {fv.lat:.6f},{fv.lon:.6f}", flush=True)
    except KeyboardInterrupt:
        fv.stop()


if __name__ == "__main__":
    main()
