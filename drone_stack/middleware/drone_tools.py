"""DroneTools: every tool the LLM can call, with the safety gate in front.

This module is transport-agnostic plain Python. `mcp_server.py` wraps each
public method as an MCP tool; the unit tests call the methods directly
against a fake vehicle. Keeping the logic here (and not inside MCP
decorators) means the safety behaviour is identical whichever client is
driving: the CLI Ollama agent, Open WebUI via mcpo, Claude Desktop, ...

Call flow for an ACTION tool (e.g. `takeoff(alt_m=10)`):

    LLM -> MCP tool -> DroneTools._act()
            1. FlightGate.check_common  (actuation on? link fresh? pilot
               override / RC inhibit? rate limit?)
            2. FlightGate.check_<action> (envelope: alt, geofence, mode...)
            3. confirmation token, if the action is in confirm_actions
            4. MavlinkHub.<action>()  -> the only MAVLink writer
            5. result dict back to the LLM (ok / blocked / ack / dry_run)

READ-ONLY tools (status, telemetry, data flow, params, mission) skip the
gate entirely and never transmit anything except read requests.

Every result is a plain JSON-able dict. Tools never raise to the LLM; errors
come back as {"ok": false, "error": "..."} so the model can explain them.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from mav_node import MavNode, haversine_m
from mavlink_hub import MavlinkHub
from safety_gate import FlightGate, Verdict

# System prompt shared by the CLI agent and exposed as an MCP prompt, so any
# client (Open WebUI, Claude Desktop, ...) can load the same rules.
SYSTEM_PROMPT = """You are the flight assistant for a quadcopter (ArduPilot on a Pixhawk 1).
You fly ONLY through the provided MAVLink tools. Rules:
1. Never invent telemetry. Call get_status / get_telemetry before deciding anything.
2. Never claim an action happened unless the tool returned ok=true. Report blockers verbatim.
3. The human pilot on the RC radio always has authority. If a tool reports
   'pilot override', stop flying: only hold/brake/land/rtl are allowed until the operator clears it.
4. Some actions return confirmation_required. Read the readback to the operator and wait for
   their go-ahead. Only call again with confirm_token after the operator has agreed
   (if confirm_mode is 'operator', the operator must tell you the code).
5. Never try to work around a safety blocker (geofence, altitude ceiling, mode whitelist,
   parameter allowlist). Explain it instead.
6. Typical flight: get_status -> set_mode GUIDED -> arm -> takeoff -> goto/goto_relative -> land or rtl.
7. Be brief. Latency matters more than eloquence. If unsure, hold position (hold) and ask.
"""


def _offset_latlon(lat: float, lon: float, north_m: float, east_m: float):
    """Flat-earth offset, accurate to cm over a paddock (< 1 km)."""
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon


class DroneTools:
    def __init__(self, node: MavNode, hub: MavlinkHub, gate: FlightGate,
                 log_dir: Optional[str] = "logs", home_poll_s: float = 5.0):
        self.node, self.hub, self.gate = node, hub, gate
        self._conf_log = os.path.join(log_dir, "confirmations.jsonl") if log_dir else None
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        self._last_mission_count: Optional[int] = None
        self._stop = threading.Event()
        self._home_poll_s = home_poll_s
        if home_poll_s:
            threading.Thread(target=self._home_poller, name="home-poller", daemon=True).start()

    @classmethod
    def from_config(cls, cfg) -> "DroneTools":
        """Wire node + gate + hub from a StackConfig (see stack_config.py)."""
        mc = cfg.mavlink
        node = MavNode(mc.url, record_dir=os.path.join(cfg.server.log_dir, "csv"),
                       baud=mc.baud, source_system=mc.source_system,
                       source_component=mc.source_component,
                       send_heartbeat=mc.send_heartbeat, request_streams=mc.request_streams,
                       stream_rate_hz=mc.stream_rate_hz, recent_buffer=mc.recent_buffer,
                       heartbeat_timeout_s=mc.heartbeat_timeout_s,
                       expected_sysid=mc.vehicle_sysid)
        gate = FlightGate(cfg.safety)
        hub = MavlinkHub(node, flight_gate=gate, dry_run=cfg.safety.dry_run,
                         log_dir=cfg.server.log_dir)
        node.start(wait_heartbeat=False)
        return cls(node, hub, gate, log_dir=cfg.server.log_dir)

    def close(self):
        self._stop.set()
        self.node.stop()

    # ------------------------------------------------------------ background
    def _home_poller(self):
        """Keep HOME_POSITION fresh: the geofence check is relative to home,
        and ArduPilot resets home when it arms."""
        was_armed = None
        while not self._stop.wait(self._home_poll_s):
            if not self.node.link_ok():
                continue
            armed = self.node.state.get("armed")
            if self.node.home() is None or armed != was_armed:
                try:
                    self.hub.request_message(242)      # HOME_POSITION
                except Exception:
                    pass
            was_armed = armed

    # =============================================================== READ-ONLY
    def get_status(self) -> dict:
        """Short health summary: link, mode, armed, GPS, battery, altitude."""
        s = self.node.get_status()
        keys = ("link_ok", "heartbeat_age_s", "mode", "armed", "fix_type", "battery",
                "voltage", "lat", "lon", "rel_alt", "heading", "rc_link_ok",
                "dist_from_home_m", "vehicle_sysid", "landed_state", "msg_age_s")
        out = {k: s.get(k) for k in keys}
        out["home"] = self.node.home()
        out["pilot_override"] = self.gate.pilot_override()
        out["actuation_enabled"] = self.gate.cfg.actuation_enabled
        out["dry_run"] = self.hub.dry_run
        if not s.get("link_ok"):
            out["note"] = ("no fresh heartbeat from the flight controller - is MAVProxy "
                           "running and forwarding to this server?")
        return out

    def get_telemetry(self) -> dict:
        """Full grouped telemetry: attitude, position, GPS, battery, VFR HUD,
        EKF flags, RC channels, home, mission seq, last STATUSTEXT lines."""
        return self.node.get_telemetry()

    def get_data_flow(self) -> dict:
        """Link/data-flow health: msg rates per type, packet loss, heartbeat
        age per source, bytes/s."""
        snap = self.node.stats.snapshot()
        snap["link_ok"] = self.node.link_ok()
        snap["connection"] = self.node.connection
        return snap

    def get_recent_messages(self, msg_type: Optional[str] = None, limit: int = 20) -> dict:
        """Last N raw MAVLink messages (optionally one type, e.g. STATUSTEXT)."""
        limit = max(1, min(int(limit), 200))
        if msg_type:
            msgs = list(self.node.recent_by_type.get(msg_type.upper(), []))
        else:
            msgs = list(self.node.recent)
        return {"count": len(msgs[-limit:]), "messages": msgs[-limit:]}

    def get_params(self, names: List[str]) -> dict:
        """Read parameters by exact name (max 20 per call). Unknown names -> null."""
        names = [n.upper() for n in names][:20]
        return {"params": {n: self.hub.get_param(n).get("value") for n in names}}

    def get_mission(self) -> dict:
        """Download the mission currently stored on the flight controller."""
        r = self.hub.download_mission()
        if r.get("ok"):
            self._last_mission_count = max(0, r["count"] - 1)
        return r

    def get_safety_limits(self) -> dict:
        """The envelope the gate enforces + pending confirmations."""
        d = self.gate.describe()
        pend = self.gate.confirmations.pending()
        if self.gate.cfg.confirm_mode == "operator":
            for p in pend:
                p["token"] = "<shown on operator console>"
        d["pending_confirmations"] = pend
        return d

    # =============================================================== ACTIONS
    def _act(self, action: str, args: Dict[str, Any], *,
             checks: Callable[[dict, dict], Verdict],
             execute: Callable[[], dict], readback: str,
             safe: bool = False, confirm_token: Optional[str] = None) -> dict:
        """Gate -> (confirm) -> hub. The single path for every action tool."""
        try:
            status, telem = self.node.get_status(), self.node.get_telemetry()
            common = self.gate.check_common(action, status, telem, safe=safe)
            specific = checks(status, telem)
            reasons = common.reasons + specific.reasons
            if reasons:
                return {"ok": False, "blocked": True, "action": action, "reasons": reasons}
            if not safe and self.gate.needs_confirmation(action):
                if not self.gate.confirmations.consume(confirm_token, action, args):
                    return self._ask_confirmation(action, args, readback,
                                                  bad_token=bool(confirm_token))
            result = execute()
            if not safe:
                self.gate.rate.record()
            result.setdefault("action", action)
            if self.hub.dry_run:
                result["dry_run"] = True
                result["note"] = "DRY RUN: gate passed, nothing was transmitted"
            return result
        except Exception as exc:    # never raise into the LLM
            return {"ok": False, "action": action, "error": f"{type(exc).__name__}: {exc}"}

    def _ask_confirmation(self, action, args, readback, bad_token=False) -> dict:
        token = self.gate.confirmations.request(action, args, readback)
        entry = {"t": round(time.time(), 1), "action": action, "args": args,
                 "readback": readback, "token": token}
        if self._conf_log:
            with open(self._conf_log, "a") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        out = {"ok": False, "confirmation_required": True, "action": action,
               "readback": readback, "expires_in_s": self.gate.cfg.confirm_ttl_s}
        if bad_token:
            out["warning"] = "the confirm_token given was wrong, expired or for other arguments"
        if self.gate.cfg.confirm_mode == "echo":
            out["confirm_token"] = token
            out["instructions"] = ("Tell the operator the readback. After they agree, call "
                                   f"{action} again with the same arguments and "
                                   f"confirm_token='{token}'.")
        else:
            # Operator mode: the LLM never sees the token. A human reads it off
            # the MCP server console / monitor and gives it to the model.
            print(f"\n*** CONFIRM {action}: {readback}  ->  code {token} ***\n",
                  file=sys.stderr, flush=True)
            out["instructions"] = ("Read the readback to the operator. The confirmation code is "
                                   "shown on the ground-station console; ask the operator for it "
                                   f"and then call {action} again with confirm_token=<code>.")
        return out

    # ---- arming ------------------------------------------------------------
    def arm(self, confirm_token: Optional[str] = None) -> dict:
        """Arm the motors (needs confirmation). Vehicle should be in GUIDED."""
        return self._act("arm", {}, confirm_token=confirm_token,
                         checks=lambda s, t: self.gate.check_arm(s, t),
                         execute=lambda: self.hub.arm(True),
                         readback="ARM the motors. Props will spin. Area clear?")

    def disarm(self) -> dict:
        """Disarm (refused while airborne - land first)."""
        return self._act("disarm", {}, checks=lambda s, t: self.gate.check_disarm(s),
                         execute=lambda: self.hub.arm(False), readback="DISARM")

    # ---- modes -------------------------------------------------------------
    def set_mode(self, mode: str, confirm_token: Optional[str] = None) -> dict:
        """Change flight mode (whitelisted modes only)."""
        mode = mode.upper().strip()
        if mode == "AUTO":
            # AUTO flies whatever is stored on the FC: never a bare mode switch.
            if "AUTO" not in self.gate.cfg.allowed_modes:
                return {"ok": False, "blocked": True, "action": "set_mode",
                        "reasons": ["AUTO is not an LLM-allowed mode: use start_mission "
                                    "(re-reads and validates the stored mission, needs "
                                    "confirmation)"]}
            return self.start_mission(confirm_token)
        return self._act("set_mode", {"mode": mode}, confirm_token=confirm_token,
                         safe=self.gate.is_safe_mode(mode),
                         checks=lambda s, t: self.gate.check_mode(mode),
                         execute=lambda: self.hub.send_mode(mode),
                         readback=f"change flight mode to {mode}")

    def hold(self) -> dict:
        """Stop and hover here. Always allowed - the 'stop' button.

        * In GUIDED and airborne: re-target the CURRENT position, staying in
          GUIDED. This does not depend on the RC sticks.
        * Otherwise: switch to LOITER. NOTE: LOITER obeys the throttle stick -
          with the stick at minimum the copter descends (in SITL RC3 sits at
          1000 unless you run `rc 3 1500` in MAVProxy). The pilot normally
          has the sticks centred, so this is the expected hand-over mode.
        """
        s = self.node.get_status()
        if (s.get("mode") == "GUIDED" and s.get("armed") and s.get("lat") is not None
                and (s.get("rel_alt") or 0) > 1.0):
            lat, lon, alt = s["lat"], s["lon"], float(s["rel_alt"])
            return self._act("hold", {}, safe=True,
                             checks=lambda st, t: Verdict(True, []),
                             execute=lambda: dict(self.hub.goto(lat, lon, alt),
                                                  held_at={"lat": lat, "lon": lon,
                                                           "alt_m": round(alt, 2)}),
                             readback="hold position")
        return self.set_mode("LOITER")

    def brake(self) -> dict:
        """Stop as fast as possible and hold (BRAKE). Always allowed."""
        return self.set_mode("BRAKE")

    def land(self) -> dict:
        """Land where it is (LAND). Always allowed."""
        return self.set_mode("LAND")

    def rtl(self) -> dict:
        """Return to launch (RTL). Always allowed."""
        return self.set_mode("RTL")

    # ---- flying ------------------------------------------------------------
    def takeoff(self, alt_m: float, confirm_token: Optional[str] = None) -> dict:
        """Take off to alt_m metres above home (GUIDED + armed; confirmation)."""
        alt_m = float(alt_m)
        return self._act("takeoff", {"alt_m": alt_m}, confirm_token=confirm_token,
                         checks=lambda s, t: self.gate.check_takeoff(alt_m, s),
                         execute=lambda: self.hub.takeoff(alt_m),
                         readback=f"TAKE OFF to {alt_m:.1f} m above home")

    def goto(self, lat: float, lon: float, alt_m: float, wait_s: float = 0) -> dict:
        """Fly to lat/lon at alt_m above home (GUIDED). Optionally wait up to
        wait_s seconds (max 120) and report whether it arrived (< 2 m)."""
        lat, lon, alt_m = float(lat), float(lon), float(alt_m)

        def execute():
            r = self.hub.goto(lat, lon, alt_m)
            return self._maybe_wait(r, lat, lon, wait_s)
        return self._act("goto", {"lat": lat, "lon": lon, "alt_m": alt_m},
                         checks=lambda s, t: self.gate.check_goto(lat, lon, alt_m, s,
                                                                  self.node.home()),
                         execute=execute, readback=f"go to {lat:.6f},{lon:.6f} @ {alt_m} m")

    def goto_relative(self, north_m: float, east_m: float, alt_m: Optional[float] = None,
                      wait_s: float = 0) -> dict:
        """Move north_m / east_m metres from the CURRENT position (negative =
        south/west). alt_m = target altitude above home (default: keep)."""
        s = self.node.get_status()
        if s.get("lat") is None:
            return {"ok": False, "error": "no position yet"}
        lat, lon = _offset_latlon(s["lat"], s["lon"], float(north_m), float(east_m))
        alt = float(alt_m) if alt_m is not None else float(s.get("rel_alt") or 0.0)
        r = self.goto(lat, lon, alt, wait_s)
        r["target"] = {"lat": round(lat, 7), "lon": round(lon, 7), "alt_m": alt}
        return r

    def _maybe_wait(self, r: dict, lat, lon, wait_s) -> dict:
        wait_s = max(0.0, min(float(wait_s or 0), 120.0))
        if not r.get("ok") or wait_s == 0 or self.hub.dry_run:
            return r
        t0 = time.time()
        while time.time() - t0 < wait_s:
            s = self.node.get_status()
            if self.gate.pilot_override():
                r.update(arrived=False, stopped="pilot override during goto")
                return r
            d = haversine_m(s["lat"], s["lon"], lat, lon)
            if d < 2.0:
                r.update(arrived=True, seconds=round(time.time() - t0, 1))
                return r
            time.sleep(0.5)
        r.update(arrived=False, remaining_m=round(d, 1), note="still flying; poll get_status")
        return r

    def set_speed(self, speed_ms: float) -> dict:
        """Set horizontal ground speed for GUIDED/AUTO (m/s)."""
        speed_ms = float(speed_ms)
        return self._act("set_speed", {"speed_ms": speed_ms},
                         checks=lambda s, t: self.gate.check_speed(speed_ms),
                         execute=lambda: self.hub.set_speed(speed_ms),
                         readback=f"set speed {speed_ms} m/s")

    # ---- parameters --------------------------------------------------------
    def set_param(self, name: str, value: float, confirm_token: Optional[str] = None) -> dict:
        """Write one allowlisted parameter (with read-back; confirmation)."""
        name, value = name.upper(), float(value)
        return self._act("set_param", {"name": name, "value": value},
                         confirm_token=confirm_token,
                         checks=lambda s, t: self.gate.check_param(name, value),
                         execute=lambda: self.hub.set_param(name, value),
                         readback=f"set parameter {name} = {value}")

    # ---- missions ----------------------------------------------------------
    def upload_mission(self, waypoints: List[Dict[str, float]], rtl_at_end: bool = True,
                       confirm_token: Optional[str] = None) -> dict:
        """Upload waypoints [{lat, lon, alt_m}, ...] (alt above home). Each
        point is checked against the geofence + altitude envelope."""
        wps = [{"lat": float(w["lat"]), "lon": float(w["lon"]), "alt_m": float(w["alt_m"])}
               for w in waypoints]

        def execute():
            r = self.hub.upload_mission(wps, rtl_at_end=rtl_at_end)
            if r.get("ok"):
                self._last_mission_count = len(wps)
            return r
        return self._act("upload_mission", {"waypoints": wps, "rtl_at_end": rtl_at_end},
                         confirm_token=confirm_token,
                         checks=lambda s, t: self.gate.check_mission(wps, self.node.home()),
                         execute=execute,
                         readback=f"upload {len(wps)} waypoints"
                                  f"{' + RTL' if rtl_at_end else ''}")

    def start_mission(self, confirm_token: Optional[str] = None) -> dict:
        """Start the mission stored on the FC (switch to AUTO; must be airborne).
        The stored mission is downloaded and validated first (geofence, altitude,
        allowed commands); the confirmation token is bound to its fingerprint."""
        dl = self.hub.download_mission()
        if not dl.get("ok"):
            return {"ok": False, "blocked": True, "action": "start_mission",
                    "reasons": [f"could not read the stored mission to validate it: "
                                f"{dl.get('error', 'unknown error')}"]}
        items = dl["items"]
        stored = self.gate.check_stored_mission(items, self.node.home())
        self._last_mission_count = max(0, dl["count"] - 1)
        digest = self.gate.mission_digest(items)
        def checks(s, t):
            v = self.gate.check_start_mission(s, self._last_mission_count)
            reasons = stored.reasons + v.reasons
            return Verdict(not reasons, reasons)
        return self._act("start_mission", {"mission": digest}, confirm_token=confirm_token,
                         checks=checks,
                         execute=lambda: self.hub.send_mode("AUTO"),
                         readback=f"start the stored mission (AUTO): {self._last_mission_count}"
                                  f" items, fingerprint {digest}")

    def clear_pilot_override(self, confirm_token: Optional[str] = None) -> dict:
        """Hand control back to the LLM after the pilot took over (confirmation)."""
        o = self.gate.pilot_override()
        if not o:
            return {"ok": True, "note": "no pilot override active"}
        # Deliberately NOT gated by check_common's override check (that would
        # deadlock); still needs actuation enabled + confirmation.
        if not self.gate.cfg.actuation_enabled:
            return {"ok": False, "blocked": True, "reasons": ["actuation disabled"]}
        if not self.gate.confirmations.consume(confirm_token, "clear_pilot_override", {}):
            return self._ask_confirmation("clear_pilot_override", {},
                                          f"give control back to the LLM ({o['reason']})",
                                          bad_token=bool(confirm_token))
        self.gate.clear_pilot_override()
        return {"ok": True, "note": "pilot override cleared; LLM actions allowed again"}


# Names grouped for the MCP layer / tests.
READ_ONLY_TOOLS = ["get_status", "get_telemetry", "get_data_flow", "get_recent_messages",
                   "get_params", "get_mission", "get_safety_limits"]
SAFE_ACTION_TOOLS = ["hold", "brake", "land", "rtl"]
ACTION_TOOLS = ["arm", "disarm", "set_mode", "takeoff", "goto", "goto_relative",
                "set_speed", "set_param", "upload_mission", "start_mission",
                "clear_pilot_override"]
ALL_TOOLS = READ_ONLY_TOOLS + SAFE_ACTION_TOOLS + ACTION_TOOLS
