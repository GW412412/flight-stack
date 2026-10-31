"""MAVLink node: the READ side of the ground-station link.

One MavNode owns one pymavlink connection. A background thread reads every
message and keeps:

  * `state`      - flat status dict (legacy keys used by SafetyGate/tools.py:
                   mode, armed, battery, fix_type, lat, lon, rel_alt, ...)
  * `telem`      - grouped telemetry (attitude, gps, battery, ekf, rc, home ...)
  * `stats`      - LinkStats: per-type rates, packet loss, heartbeat age
  * `recent`     - ring buffer of the last N messages (data-flow tool)
  * `params`     - cache of PARAM_VALUE replies
  * waiters      - lets the hub block until e.g. a COMMAND_ACK arrives,
                   without a second thread fighting over recv_match()

Talks to the aircraft through MAVProxy's fan-out (see
drone_stack/mavproxy/start_mavproxy.sh), never by opening the serial port
itself while MAVProxy is running - only ONE program may own /dev/ttyACM0.

AUTHORITY RULE (docs/INTEGRATION.md): MavNode does not decide anything and
does not send commands. All COMMAND_LONG / SET_POSITION_TARGET / PARAM_SET /
mission writes go through `mavlink_hub.MavlinkHub`, the single writer. The
only things MavNode transmits itself are housekeeping: our own GCS heartbeat,
optional stream requests, and RTCM passthrough (GPS corrections, not a
command).
"""
from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
import warnings
from collections import deque
from datetime import datetime
from typing import Callable, Deque, Iterable, List, Optional

# ArduPilot speaks MAVLink 2. Without this pymavlink loads the MAVLink 1
# dialect until it happens to see a v2 packet, and v2-only fields such as
# mission_type / COMMAND_ACK.target_system would be missing. Must be set
# before pymavlink is imported.
os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil  # noqa: E402

from link_stats import LinkStats

M = mavutil.mavlink

# EKF_STATUS_REPORT.flags bit names (MAVLink ESTIMATOR_STATUS_FLAGS).
EKF_FLAG_NAMES = {
    1: "attitude", 2: "velocity_horiz", 4: "velocity_vert", 8: "pos_horiz_rel",
    16: "pos_horiz_abs", 32: "pos_vert_abs", 64: "pos_vert_agl",
    128: "const_pos_mode", 256: "pred_pos_horiz_rel", 512: "pred_pos_horiz_abs",
    1024: "uninitialized", 32768: "gps_glitch",
}
# What Guided/Loiter needs from the EKF before we let the LLM move the drone.
EKF_POSITION_OK = 1 | 2 | 4 | 16 | 32

# Per-message freshness. A live HEARTBEAT only proves the autopilot is up, not
# that position/attitude/battery are still arriving (a stream can die alone).
STREAM_OF_MSG = {
    "GLOBAL_POSITION_INT": "position", "ATTITUDE": "attitude", "SYS_STATUS": "sys_status",
    "GPS_RAW_INT": "gps", "EKF_STATUS_REPORT": "ekf", "EXTENDED_SYS_STATE": "landed",
}
LANDED_STATE_NAMES = {0: "UNDEFINED", 1: "ON_GROUND", 2: "IN_AIR", 3: "TAKEOFF", 4: "LANDING"}

RC_RECEIVER_BIT = M.MAV_SYS_STATUS_SENSOR_RC_RECEIVER  # 0x10000


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres (good to cm at paddock scale)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class _Waiter:
    """A one-shot subscription: 'tell me about the next X matching Y'."""

    def __init__(self, types: Iterable[str], predicate: Optional[Callable] = None):
        self.types = set(types)
        self.predicate = predicate
        self.q: "queue.Queue" = queue.Queue()


class MavNode:
    def __init__(self, connection: str = "udp:127.0.0.1:14551",
                 record_dir: Optional[str] = "recordings", *,
                 baud: int = 57600,
                 source_system: int = 250, source_component: int = 191,
                 send_heartbeat: bool = False, request_streams: bool = False,
                 stream_rate_hz: int = 4, recent_buffer: int = 500,
                 heartbeat_timeout_s: float = 3.0, conn=None,
                 expected_sysid: Optional[int] = None):
        """`connection` is any pymavlink URL: udpin:HOST:PORT (listen for
        MAVProxy --out), tcp:127.0.0.1:5760 (SITL direct), /dev/ttyACM0 (FC
        direct, only if nothing else owns the port). `conn` lets tests inject
        an already-open connection. `record_dir=None` disables the CSV."""
        self.connection = connection
        self.conn = conn or mavutil.mavlink_connection(
            connection, baud=baud, source_system=source_system,
            source_component=source_component, autoreconnect=True)
        self.record_dir = record_dir
        if record_dir:
            os.makedirs(record_dir, exist_ok=True)
        self.send_heartbeat = send_heartbeat
        self.request_streams = request_streams
        self.stream_rate_hz = stream_rate_hz
        self.heartbeat_timeout_s = heartbeat_timeout_s
        # 0/None = pin the first autopilot heartbeat; otherwise only that sysid.
        self.expected_sysid = expected_sysid or None

        # Legacy flat state (tools.py / SafetyGate.can_arm read these keys).
        self.state = {
            "messages": [],           # last STATUSTEXT strings (bounded below)
            "battery": None,          # None = unknown (never a made-up number)
            "rc_link_ok": None,
            "video_trusted": None,
            "live_id_enabled": None,
            "heartbeat_age_s": None,
        }
        self.telem: dict = {}          # grouped telemetry, see _handle()
        self.params: dict = {}         # name -> value cache
        self.stats = LinkStats()
        # Two views of recent traffic: everything (a couple of seconds at
        # 10 Hz streams) and the last 20 of EACH type, so rare messages such as
        # COMMAND_ACK or STATUSTEXT are not pushed out by ATTITUDE spam.
        self.recent: Deque[dict] = deque(maxlen=recent_buffer)
        self.recent_by_type: dict = {}
        self.statustext: Deque[dict] = deque(maxlen=50)

        # pymavlink connections are not safe for concurrent writers; every
        # transmit (heartbeat thread, hub) takes this lock.
        self.send_lock = threading.Lock()
        self._waiters: List[_Waiter] = []
        self._waiters_lock = threading.Lock()
        self._mode_listeners: List[Callable[[Optional[str], str], None]] = []
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._csv = None
        self._last_hb: Optional[float] = None
        self._seen: dict = {}          # stream name -> time.monotonic() of last message
        self.vehicle_sysid: Optional[int] = None
        self.vehicle_compid: Optional[int] = None
        self.vehicle_type: Optional[int] = None

    # ------------------------------------------------------------ lifecycle
    def start(self, wait_heartbeat: bool = True, timeout: float = 30) -> "MavNode":
        """Start reader (+ heartbeat) threads. With wait_heartbeat=False the
        call returns immediately; tools then report 'link down' until the FC
        is heard - the MCP server uses this so it can start before the drone."""
        if self.record_dir:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._csv = open(os.path.join(self.record_dir, f"telemetry_{stamp}.csv"),
                             "w", newline="")
            self._csv.write("timestamp,msg_type,lat,lon,alt_m,heading_deg,battery_pct\n")
        self._spawn(self._reader, "mav-reader")
        if self.send_heartbeat or self.request_streams:
            self._spawn(self._housekeeping, "mav-heartbeat")
        if wait_heartbeat:
            print("Waiting for heartbeat...")
            t0 = time.time()
            while self.vehicle_sysid is None and time.time() - t0 < timeout:
                time.sleep(0.1)
            if self.vehicle_sysid is None:
                raise TimeoutError(f"no autopilot heartbeat on {self.connection} in {timeout}s")
            print(f"Heartbeat from system {self.vehicle_sysid}")
        return self

    def _spawn(self, fn, name):
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self):
        self._stop.set()
        if self._csv:
            self._csv.close()
            self._csv = None

    # ------------------------------------------------------------ housekeeping
    def _housekeeping(self):
        """1 Hz: our own heartbeat (as a GCS, sysid 250 by default) and, when
        talking to the FC directly, a periodic stream request."""
        last_req = 0.0
        while not self._stop.is_set():
            with self.send_lock:
                if self.send_heartbeat:
                    self.conn.mav.heartbeat_send(M.MAV_TYPE_GCS, M.MAV_AUTOPILOT_INVALID,
                                                 0, 0, M.MAV_STATE_ACTIVE)
                if (self.request_streams and self.vehicle_sysid is not None
                        and time.time() - last_req > 10):
                    self.conn.mav.request_data_stream_send(
                        self.vehicle_sysid, self.vehicle_compid or 1,
                        M.MAV_DATA_STREAM_ALL, self.stream_rate_hz, 1)
                    last_req = time.time()
            self._stop.wait(1.0)

    # ------------------------------------------------------------ reader
    def _reader(self):
        while not self._stop.is_set():
            try:
                msg = self.conn.recv_match(blocking=True, timeout=0.5)
            except Exception as exc:   # serial unplugged etc. - keep trying
                self.state["link_error"] = f"{type(exc).__name__}: {exc}"
                time.sleep(0.5)
                continue
            if msg is None:
                continue
            self.stats.observe(msg)
            if msg.get_type() == "BAD_DATA":
                continue
            try:
                self._handle(msg)
            except Exception as exc:   # never let one odd packet kill the reader
                self.state["last_handler_error"] = f"{msg.get_type()}: {exc}"
            if self._from_vehicle(msg):         # ACKs etc. only from the pinned FC
                self._dispatch_waiters(msg)

    def _from_vehicle(self, msg) -> bool:
        """True only if the packet comes from the pinned autopilot (sysid AND
        compid). Until the first autopilot heartbeat pins them nothing is
        adopted, so another system on the link cannot feed us state first."""
        if self.vehicle_sysid is None:
            return False
        return (msg.get_srcSystem() == self.vehicle_sysid
                and msg.get_srcComponent() == self.vehicle_compid)

    def _handle(self, msg):
        t = msg.get_type()
        now = time.time()
        rec = {"t": round(now, 3), "type": t,
               "src": f"{msg.get_srcSystem()}:{msg.get_srcComponent()}",
               "data": _msg_dict(msg)}
        self.recent.append(rec)
        self.recent_by_type.setdefault(t, deque(maxlen=20)).append(rec)
        if t == "HEARTBEAT":
            # Ignore heartbeats from other GCSs (QGC 255, MAVProxy, ourselves).
            if msg.type == M.MAV_TYPE_GCS or msg.autopilot == M.MAV_AUTOPILOT_INVALID:
                return
            if self.vehicle_sysid is None:
                if self.expected_sysid and msg.get_srcSystem() != self.expected_sysid:
                    return                      # configured sysid: never pin another
                self.vehicle_sysid = msg.get_srcSystem()
                self.vehicle_compid = msg.get_srcComponent()
                # pymavlink uses target_system for mode_mapping() etc.
                self.conn.target_system = self.vehicle_sysid
                self.conn.target_component = self.vehicle_compid
            if not self._from_vehicle(msg):
                return
            self.vehicle_type = msg.type
            self._last_hb = now
            old_mode = self.state.get("mode")
            mode = mavutil.mode_string_v10(msg)
            self.state.update(mode=mode,
                              armed=bool(msg.base_mode & M.MAV_MODE_FLAG_SAFETY_ARMED),
                              system_status=msg.system_status,
                              heartbeat_age_s=0.0)
            if old_mode is not None and old_mode != mode:
                for cb in list(self._mode_listeners):
                    try:
                        cb(old_mode, mode)
                    except Exception:
                        pass
            return
        if not self._from_vehicle(msg):
            return
        if t in STREAM_OF_MSG:
            self._seen[STREAM_OF_MSG[t]] = time.monotonic()
        if t == "GLOBAL_POSITION_INT":
            self.state.update(lat=msg.lat / 1e7, lon=msg.lon / 1e7, alt=msg.alt / 1e3,
                              rel_alt=msg.relative_alt / 1e3, heading=msg.hdg / 100.0)
            self.telem["position"] = {
                "lat": msg.lat / 1e7, "lon": msg.lon / 1e7, "alt_msl_m": msg.alt / 1e3,
                "rel_alt_m": msg.relative_alt / 1e3, "heading_deg": msg.hdg / 100.0,
                "vx_ms": msg.vx / 100.0, "vy_ms": msg.vy / 100.0, "vz_ms": msg.vz / 100.0}
        elif t == "ATTITUDE":
            self.telem["attitude"] = {
                "roll_deg": round(math.degrees(msg.roll), 2),
                "pitch_deg": round(math.degrees(msg.pitch), 2),
                "yaw_deg": round(math.degrees(msg.yaw), 2)}
        elif t == "SYS_STATUS":
            # battery_remaining -1 = FC does not know -> None, never a number.
            pct = msg.battery_remaining if 0 <= msg.battery_remaining <= 100 else None
            self.state.update(battery=pct, voltage=msg.voltage_battery / 1000.0)
            self.telem["battery"] = {
                "remaining_pct": pct,
                "voltage_v": msg.voltage_battery / 1000.0,
                "current_a": msg.current_battery / 100.0 if msg.current_battery >= 0 else None}
            # RC receiver health as reported by the FC itself (most reliable
            # signal that the pilot's ELRS link is up).
            if msg.onboard_control_sensors_present & RC_RECEIVER_BIT:
                self.state["rc_link_ok"] = bool(msg.onboard_control_sensors_health & RC_RECEIVER_BIT)
        elif t == "GPS_RAW_INT":
            self.state["fix_type"] = msg.fix_type
            self.telem["gps"] = {"fix_type": msg.fix_type,
                                 "satellites": msg.satellites_visible,
                                 "hdop": msg.eph / 100.0 if msg.eph != 65535 else None}
        elif t == "EXTENDED_SYS_STATE":
            self.state["landed_state"] = msg.landed_state
        elif t == "VFR_HUD":
            self.telem["vfr_hud"] = {"groundspeed_ms": round(msg.groundspeed, 2),
                                     "airspeed_ms": round(msg.airspeed, 2),
                                     "climb_ms": round(msg.climb, 2),
                                     "throttle_pct": msg.throttle}
        elif t == "EKF_STATUS_REPORT":
            flags = msg.flags
            self.telem["ekf"] = {
                "flags": flags,
                "flag_names": [n for b, n in EKF_FLAG_NAMES.items() if flags & b],
                "position_ok": (flags & EKF_POSITION_OK) == EKF_POSITION_OK and not flags & 1024,
                "velocity_variance": round(msg.velocity_variance, 3),
                "pos_horiz_variance": round(msg.pos_horiz_variance, 3),
                "compass_variance": round(msg.compass_variance, 3)}
        elif t in ("RC_CHANNELS", "RC_CHANNELS_RAW"):
            chans = [getattr(msg, f"chan{i}_raw", 0) for i in range(1, 19)
                     if hasattr(msg, f"chan{i}_raw")]
            rssi = getattr(msg, "rssi", 255)
            self.telem["rc"] = {"channels": chans, "rssi": rssi,
                                "chancount": getattr(msg, "chancount", len(chans))}
            # rssi: 0 = no signal, 255 = unknown (SITL / no RSSI wired).
            if rssi == 0:
                self.state["rc_link_ok"] = False
            elif rssi != 255 and self.state.get("rc_link_ok") is None:
                self.state["rc_link_ok"] = True
            self.state["rc_rssi"] = rssi
        elif t == "HOME_POSITION":
            self.telem["home"] = {"lat": msg.latitude / 1e7, "lon": msg.longitude / 1e7,
                                  "alt_msl_m": msg.altitude / 1e3}
        elif t == "MISSION_CURRENT":
            self.telem["mission"] = {"current_seq": msg.seq}
        elif t == "PARAM_VALUE":
            self.params[msg.param_id] = msg.param_value
        elif t == "STATUSTEXT":
            self.statustext.append({"t": round(now, 1), "severity": msg.severity, "text": msg.text})
            self.state["messages"] = [s["text"] for s in self.statustext]
        elif t == "CAMERA_FEEDBACK":
            self.state["last_camera"] = {"lat": msg.lat / 1e7, "lon": msg.lng / 1e7,
                                         "alt": msg.alt_msl}
        if self._csv and t in ("GLOBAL_POSITION_INT", "SYS_STATUS"):
            self._csv.write(
                f"{now},{t},{self.state.get('lat','')},{self.state.get('lon','')},"
                f"{self.state.get('rel_alt','')},{self.state.get('heading','')},"
                f"{'' if self.state.get('battery') is None else self.state['battery']}\n")
            self._csv.flush()

    # ------------------------------------------------------------ waiters
    def _dispatch_waiters(self, msg):
        with self._waiters_lock:
            waiters = list(self._waiters)
        for w in waiters:
            if msg.get_type() in w.types and (w.predicate is None or w.predicate(msg)):
                w.q.put(msg)

    def subscribe(self, types: Iterable[str], predicate: Optional[Callable] = None) -> _Waiter:
        """Register BEFORE sending a request, so the reply cannot be missed."""
        w = _Waiter(types, predicate)
        with self._waiters_lock:
            self._waiters.append(w)
        return w

    def unsubscribe(self, w: _Waiter) -> None:
        with self._waiters_lock:
            if w in self._waiters:
                self._waiters.remove(w)

    def wait(self, w: _Waiter, timeout: float):
        """Next message matching the subscription, or None on timeout."""
        try:
            return w.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def add_mode_listener(self, cb: Callable[[Optional[str], str], None]) -> None:
        """cb(old_mode, new_mode) on every vehicle mode change."""
        self._mode_listeners.append(cb)

    # ------------------------------------------------------------ read API
    def link_ok(self) -> bool:
        age = self.heartbeat_age()
        return age is not None and age <= self.heartbeat_timeout_s

    def heartbeat_age(self) -> Optional[float]:
        return None if self._last_hb is None else time.time() - self._last_hb

    def msg_ages(self) -> dict:
        """Seconds since the last position/attitude/sys_status/gps/ekf/landed
        message from the pinned vehicle (None = never received)."""
        now = time.monotonic()
        return {name: (round(now - self._seen[name], 2) if name in self._seen else None)
                for name in STREAM_OF_MSG.values()}

    def home(self) -> Optional[dict]:
        return self.telem.get("home")

    def get_status(self) -> dict:
        """Flat snapshot (legacy shape) + link and home info."""
        s = dict(self.state)
        s["timestamp"] = time.time()
        if self._last_hb:
            s["heartbeat_age_s"] = round(time.time() - self._last_hb, 2)
        s["link_ok"] = self.link_ok()
        ages = self.msg_ages()
        s["msg_age_s"] = ages
        # landed state is only reported while its own message is fresh
        ls = s.pop("landed_state", None)
        fresh = ages["landed"] is not None and ages["landed"] <= self.heartbeat_timeout_s
        s["landed_state"] = LANDED_STATE_NAMES.get(ls) if (fresh and ls is not None) else None
        s["vehicle_sysid"] = self.vehicle_sysid
        h = self.home()
        if h and s.get("lat") is not None:
            s["dist_from_home_m"] = round(haversine_m(h["lat"], h["lon"], s["lat"], s["lon"]), 1)
        return s

    def get_telemetry(self) -> dict:
        """Grouped telemetry for the LLM: everything it may reason about."""
        st = self.get_status()
        out = {k: st.get(k) for k in ("mode", "armed", "link_ok", "heartbeat_age_s",
                                       "rc_link_ok", "dist_from_home_m", "vehicle_sysid")}
        out.update({k: v for k, v in self.telem.items()})
        out["statustext"] = list(self.statustext)[-5:]
        return out

    def mode_mapping(self) -> dict:
        """{'GUIDED': 4, ...} for the connected vehicle type."""
        if self.vehicle_type is not None:
            m = mavutil.mode_mapping_bynumber(self.vehicle_type)
            if m:
                return {v: k for k, v in m.items()}
        return self.conn.mode_mapping() or {}

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

    # ------------------------------------------------------------ legacy writers
    def _hub(self):
        if not hasattr(self, "_legacy_hub"):
            from mavlink_hub import MavlinkHub   # lazy: avoids an import cycle
            self._legacy_hub = MavlinkHub(self)
        return self._legacy_hub

    def arm(self, arm: bool = True) -> dict:
        """DEPRECATED: kept for old callers; routed through MavlinkHub so the
        hub stays the only COMMAND_LONG writer."""
        warnings.warn("MavNode.arm is deprecated; use MavlinkHub.arm", DeprecationWarning)
        return self._hub().send_command_long(M.MAV_CMD_COMPONENT_ARM_DISARM,
                                             1.0 if arm else 0.0, label="legacy_arm")

    def set_mode(self, mode: str) -> dict:
        """DEPRECATED: routed through MavlinkHub (see arm())."""
        warnings.warn("MavNode.set_mode is deprecated; use MavlinkHub.set_mode", DeprecationWarning)
        return self._hub().send_mode(mode)

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
        with self.send_lock:
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


def _msg_dict(msg) -> dict:
    """Compact JSON-safe dict of a message (bytes/arrays made printable)."""
    d = msg.to_dict()
    d.pop("mavpackettype", None)
    for k, v in list(d.items()):
        if isinstance(v, (bytes, bytearray)):
            d[k] = v.hex()
        elif isinstance(v, list) and len(v) > 20:
            d[k] = f"<{len(v)} values>"
    return d


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Print MavNode status every 2 s")
    ap.add_argument("--url", default="udp:127.0.0.1:14551")
    a = ap.parse_args()
    node = MavNode(a.url, request_streams=a.url.startswith(("tcp:", "/dev/")))
    node.start()
    try:
        while True:
            time.sleep(2)
            print(json.dumps(node.get_status(), indent=2, default=str))
    except KeyboardInterrupt:
        node.stop()
