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
            4. MavlinkHub.<action>()  -> the only MAVLink writer, run inside
               `hub.acting_for(action)`. At the moment of the write the hub
               calls FlightGate.send_if_permitted, which re-checks pilot
               authority and sends under ONE lock (atomic check-and-send).
               If the pilot took over in the meantime nothing is sent and
               the result is {"ok": false, "blocked": true, "stage": "send"}.
            5. result dict back to the LLM (ok / blocked / ack / dry_run)

Every decision (refused at check, confirmation asked/refused, refused or
done at send) is also written to the gate's bounded audit trail, readable
with the read-only tool `get_gate_audit`.

READ-ONLY tools (status, telemetry, data flow, params, mission) skip the
gate entirely and never transmit anything except read requests.

Every result is a plain JSON-able dict. Tools never raise to the LLM; errors
come back as {"ok": false, "error": "..."} so the model can explain them.
Wrong-typed or non-finite arguments (a list for an altitude, NaN, infinity,
True for a number) are refused as {"ok": false, "blocked": true, ...} before
anything else happens; see `_tool` below.
"""
from __future__ import annotations

import functools
import json
import math
import os
import sys
import threading
import time
from contextlib import nullcontext
from typing import Any, Callable, Dict, List, Optional

from mav_node import MavNode, haversine_m
from mavlink_hub import MavlinkHub
from safety_gate import FlightGate, GateRefused, Verdict, clean_name, finite_number

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


def _tool(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Decorator for every public tool method: it can never raise.

    Any exception that escapes (a bug, a dropped serial port, a value we did
    not anticipate) becomes {"ok": False, "error": "..."}. Nothing was
    transmitted unless the hub said so, and the gate's own checks always run
    before any write, so turning an exception into a refusal is the safe
    direction."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> dict:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return {"ok": False, "action": fn.__name__,
                    "error": f"{type(exc).__name__}: {exc}"}
    return wrapper


def _refuse(action: str, reasons: List[str]) -> dict:
    """The standard 'blocked before anything was checked or sent' result."""
    return {"ok": False, "blocked": True, "action": action, "reasons": list(reasons)}


def _numbers(action: str, **values: Any):
    """Check several numeric arguments at once.

    Returns (dict of floats, None) or (None, blocked-result) so callers can
    write `vals, err = _numbers(...); if err: return err`."""
    out, bad = {}, []
    for name, v in values.items():
        f, why = finite_number(v, name)
        if why:
            bad.append(why)
        else:
            out[name] = f
    return (None, _refuse(action, bad)) if bad else (out, None)


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
                       heartbeat_timeout_s=mc.heartbeat_timeout_s)
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
    @_tool
    def get_status(self) -> dict:
        """Short health summary: link, mode, armed, GPS, battery, altitude."""
        s = self.node.get_status()
        keys = ("link_ok", "heartbeat_age_s", "mode", "armed", "fix_type", "battery",
                "voltage", "lat", "lon", "rel_alt", "heading", "rc_link_ok",
                "dist_from_home_m", "vehicle_sysid")
        out = {k: s.get(k) for k in keys}
        out["home"] = self.node.home()
        out["pilot_override"] = self.gate.pilot_override()
        out["actuation_enabled"] = self.gate.cfg.actuation_enabled
        out["dry_run"] = self.hub.dry_run
        if not s.get("link_ok"):
            out["note"] = ("no fresh heartbeat from the flight controller - is MAVProxy "
                           "running and forwarding to this server?")
        return out

    @_tool
    def get_telemetry(self) -> dict:
        """Full grouped telemetry: attitude, position, GPS, battery, VFR HUD,
        EKF flags, RC channels, home, mission seq, last STATUSTEXT lines."""
        return self.node.get_telemetry()

    @_tool
    def get_data_flow(self) -> dict:
        """Link/data-flow health: msg rates per type, packet loss, heartbeat
        age per source, bytes/s."""
        snap = self.node.stats.snapshot()
        snap["link_ok"] = self.node.link_ok()
        snap["connection"] = self.node.connection
        return snap

    @_tool
    def get_recent_messages(self, msg_type: Optional[str] = None, limit: int = 20) -> dict:
        """Last N raw MAVLink messages (optionally one type, e.g. STATUSTEXT)."""
        n, why = finite_number(limit, "limit")
        limit = 20 if why else max(1, min(int(n), 200))
        if msg_type is not None and not isinstance(msg_type, str):
            return {"ok": False, "error": "msg_type must be text, e.g. 'STATUSTEXT'"}
        if msg_type:
            msgs = list(self.node.recent_by_type.get(msg_type.upper(), []))
        else:
            msgs = list(self.node.recent)
        return {"count": len(msgs[-limit:]), "messages": msgs[-limit:]}

    @_tool
    def get_params(self, names: List[str]) -> dict:
        """Read parameters by exact name (max 20 per call). Unknown names -> null."""
        if not isinstance(names, (list, tuple)) or not all(isinstance(n, str) for n in names):
            return {"ok": False, "error": "names must be a list of parameter names (text)"}
        names = [n.upper() for n in names][:20]
        return {"params": {n: self.hub.get_param(n).get("value") for n in names}}

    @_tool
    def get_mission(self) -> dict:
        """Download the mission currently stored on the flight controller."""
        r = self.hub.download_mission()
        if r.get("ok"):
            self._last_mission_count = max(0, r["count"] - 1)
        return r

    @_tool
    def get_safety_limits(self) -> dict:
        """The envelope the gate enforces + pending confirmations."""
        d = self.gate.describe()
        pend = self.gate.confirmations.pending()
        if self.gate.cfg.confirm_mode == "operator":
            for p in pend:
                p["token"] = "<shown on operator console>"
        d["pending_confirmations"] = pend
        return d

    @_tool
    def get_gate_audit(self, limit: int = 50) -> dict:
        """READ-ONLY. The last `limit` (max = capacity) FlightGate decisions
        and authority-state changes, oldest first: what was allowed or
        refused, why, the state before/after, and what caused it (e.g. the
        mode change that latched a pilot override). Confirmation codes are
        never stored here, so this is safe to show the LLM in operator mode.
        In memory only; logs/llm-stack/mavlink-commands.jsonl is the durable
        record of what was actually sent."""
        entries = self.gate.audit.entries(limit)
        return {"ok": True, "count": len(entries), "capacity": self.gate.audit.capacity,
                "authority_state": self.gate.authority_state(), "entries": entries}

    # =============================================================== ACTIONS
    def _act(self, action: str, args: Dict[str, Any], *,
             checks: Callable[[dict, dict], Verdict],
             execute: Callable[[], dict], readback: str,
             safe: bool = False, confirm_token: Optional[str] = None) -> dict:
        """Gate -> (confirm) -> hub. The single path for every action tool.

        Steps, in the original order (nothing here loosens a check):
          1. common + action-specific checks -> refuse with reasons
          2. confirmation code, if needed -> ask / refuse with reason
          3. execute() inside hub.acting_for(action): the hub's write
             re-checks pilot authority atomically (GateRefused -> blocked)
        Each outcome is written to the gate's audit trail."""
        try:
            status, telem = self.node.get_status(), self.node.get_telemetry()
            common = self.gate.check_common(action, status, telem, safe=safe)
            specific = checks(status, telem)
            reasons = common.reasons + specific.reasons
            if reasons:
                self.gate.record_decision(action, False, reasons, stage="check", safe=safe)
                return {"ok": False, "blocked": True, "action": action, "reasons": reasons}
            if not safe and self.gate.needs_confirmation(action):
                ok, why = self.gate.confirmations.check(confirm_token, action, args)
                if not ok:
                    given = confirm_token is not None and confirm_token != ""
                    self.gate.record_decision(action, False, [why], stage="confirm")
                    return self._ask_confirmation(action, args, readback,
                                                  bad_token=given, why=why)
            acting = getattr(self.hub, "acting_for", None)
            ctx = acting(action, safe=safe) if acting else nullcontext()
            try:
                with ctx:
                    result = execute()
            except GateRefused as exc:
                # Pilot took over between step 1 and the write: nothing sent.
                return {"ok": False, "blocked": True, "action": action, "stage": "send",
                        "reasons": exc.reasons,
                        "note": "authority changed after the checks; nothing was sent"}
            if not safe:
                self.gate.rate.record()
            result.setdefault("action", action)
            if self.hub.dry_run:
                result["dry_run"] = True
                result["note"] = "DRY RUN: gate passed, nothing was transmitted"
            return result
        except Exception as exc:    # never raise into the LLM
            return {"ok": False, "action": action, "error": f"{type(exc).__name__}: {exc}"}

    def _ask_confirmation(self, action, args, readback, bad_token=False,
                          why: Optional[str] = None) -> dict:
        token = self.gate.confirmations.request(action, args, readback)
        entry = {"t": round(time.time(), 1), "action": action, "args": args,
                 "readback": readback, "token": token}
        if self._conf_log:
            with open(self._conf_log, "a") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        out = {"ok": False, "confirmation_required": True, "action": action,
               "readback": readback, "expires_in_s": self.gate.confirmations.ttl_s}
        if bad_token:
            out["warning"] = ("the confirm_token given was not accepted"
                              + (f": {why}" if why else "")
                              + ". A new code has been issued.")
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
    @_tool
    def arm(self, confirm_token: Optional[str] = None) -> dict:
        """Arm the motors (needs confirmation). Vehicle should be in GUIDED."""
        return self._act("arm", {}, confirm_token=confirm_token,
                         checks=lambda s, t: self.gate.check_arm(s, t),
                         execute=lambda: self.hub.arm(True),
                         readback="ARM the motors. Props will spin. Area clear?")

    @_tool
    def disarm(self) -> dict:
        """Disarm (refused while airborne - land first)."""
        return self._act("disarm", {}, checks=lambda s, t: self.gate.check_disarm(s),
                         execute=lambda: self.hub.arm(False), readback="DISARM")

    # ---- modes -------------------------------------------------------------
    @_tool
    def set_mode(self, mode: str, confirm_token: Optional[str] = None) -> dict:
        """Change flight mode (whitelisted modes only)."""
        m, why = clean_name(mode, "mode")
        if why:
            return _refuse("set_mode", [why])
        mode = m
        return self._act("set_mode", {"mode": mode}, confirm_token=confirm_token,
                         safe=self.gate.is_safe_mode(mode),
                         checks=lambda s, t: self.gate.check_mode(mode),
                         execute=lambda: self.hub.send_mode(mode),
                         readback=f"change flight mode to {mode}")

    @_tool
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

    @_tool
    def brake(self) -> dict:
        """Stop as fast as possible and hold (BRAKE). Always allowed."""
        return self.set_mode("BRAKE")

    @_tool
    def land(self) -> dict:
        """Land where it is (LAND). Always allowed."""
        return self.set_mode("LAND")

    @_tool
    def rtl(self) -> dict:
        """Return to launch (RTL). Always allowed."""
        return self.set_mode("RTL")

    # ---- flying ------------------------------------------------------------
    @_tool
    def takeoff(self, alt_m: float, confirm_token: Optional[str] = None) -> dict:
        """Take off to alt_m metres above home (GUIDED + armed; confirmation)."""
        vals, err = _numbers("takeoff", alt_m=alt_m)
        if err:
            return err
        alt_m = vals["alt_m"]
        return self._act("takeoff", {"alt_m": alt_m}, confirm_token=confirm_token,
                         checks=lambda s, t: self.gate.check_takeoff(alt_m, s),
                         execute=lambda: self.hub.takeoff(alt_m),
                         readback=f"TAKE OFF to {alt_m:.1f} m above home")

    @_tool
    def goto(self, lat: float, lon: float, alt_m: float, wait_s: float = 0) -> dict:
        """Fly to lat/lon at alt_m above home (GUIDED). Optionally wait up to
        wait_s seconds (max 120) and report whether it arrived (< 2 m)."""
        vals, err = _numbers("goto", lat=lat, lon=lon, alt_m=alt_m, wait_s=wait_s or 0)
        if err:
            return err
        lat, lon, alt_m, wait_s = vals["lat"], vals["lon"], vals["alt_m"], vals["wait_s"]

        def execute():
            r = self.hub.goto(lat, lon, alt_m)
            return self._maybe_wait(r, lat, lon, wait_s)
        return self._act("goto", {"lat": lat, "lon": lon, "alt_m": alt_m},
                         checks=lambda s, t: self.gate.check_goto(lat, lon, alt_m, s,
                                                                  self.node.home()),
                         execute=execute, readback=f"go to {lat:.6f},{lon:.6f} @ {alt_m} m")

    @_tool
    def goto_relative(self, north_m: float, east_m: float, alt_m: Optional[float] = None,
                      wait_s: float = 0) -> dict:
        """Move north_m / east_m metres from the CURRENT position (negative =
        south/west). alt_m = target altitude above home (default: keep)."""
        nums = {"north_m": north_m, "east_m": east_m, "wait_s": wait_s or 0}
        if alt_m is not None:
            nums["alt_m"] = alt_m
        vals, err = _numbers("goto_relative", **nums)
        if err:
            return err
        s = self.node.get_status()
        if s.get("lat") is None:
            return {"ok": False, "error": "no position yet"}
        lat, lon = _offset_latlon(s["lat"], s["lon"], vals["north_m"], vals["east_m"])
        alt = vals["alt_m"] if alt_m is not None else float(s.get("rel_alt") or 0.0)
        r = self.goto(lat, lon, alt, vals["wait_s"])
        if not isinstance(r, dict):
            return {"ok": False, "error": "unexpected goto result"}
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

    @_tool
    def set_speed(self, speed_ms: float) -> dict:
        """Set horizontal ground speed for GUIDED/AUTO (m/s)."""
        vals, err = _numbers("set_speed", speed_ms=speed_ms)
        if err:
            return err
        speed_ms = vals["speed_ms"]
        return self._act("set_speed", {"speed_ms": speed_ms},
                         checks=lambda s, t: self.gate.check_speed(speed_ms),
                         execute=lambda: self.hub.set_speed(speed_ms),
                         readback=f"set speed {speed_ms} m/s")

    # ---- parameters --------------------------------------------------------
    @_tool
    def set_param(self, name: str, value: float, confirm_token: Optional[str] = None) -> dict:
        """Write one allowlisted parameter (with read-back; confirmation)."""
        n, why = clean_name(name, "parameter name", max_len=16)
        vals, err = _numbers("set_param", value=value)
        if why or err:
            return _refuse("set_param", ([why] if why else []) + (err or {}).get("reasons", []))
        name, value = n, vals["value"]
        return self._act("set_param", {"name": name, "value": value},
                         confirm_token=confirm_token,
                         checks=lambda s, t: self.gate.check_param(name, value),
                         execute=lambda: self.hub.set_param(name, value),
                         readback=f"set parameter {name} = {value}")

    # ---- missions ----------------------------------------------------------
    @_tool
    def upload_mission(self, waypoints: List[Dict[str, float]], rtl_at_end: bool = True,
                       confirm_token: Optional[str] = None) -> dict:
        """Upload waypoints [{lat, lon, alt_m}, ...] (alt above home). Each
        point is checked against the geofence + altitude envelope."""
        if not isinstance(waypoints, (list, tuple)):
            return _refuse("upload_mission", ["waypoints must be a list of {lat, lon, alt_m}"])
        if not isinstance(rtl_at_end, bool):
            return _refuse("upload_mission", ["rtl_at_end must be true or false"])
        wps, bad = [], []
        for i, w in enumerate(waypoints):
            if not isinstance(w, dict) or not all(k in w for k in ("lat", "lon", "alt_m")):
                bad.append(f"wp{i + 1}: must be an object with lat, lon and alt_m")
                continue
            vals, err = _numbers("upload_mission", **{f"wp{i + 1} {k}": w[k]
                                                      for k in ("lat", "lon", "alt_m")})
            if err:
                bad += err["reasons"]
                continue
            wps.append({"lat": vals[f"wp{i + 1} lat"], "lon": vals[f"wp{i + 1} lon"],
                        "alt_m": vals[f"wp{i + 1} alt_m"]})
        if bad:
            return _refuse("upload_mission", bad)

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

    @_tool
    def start_mission(self, confirm_token: Optional[str] = None) -> dict:
        """Start the uploaded mission (switch to AUTO; must be airborne)."""
        return self._act("start_mission", {}, confirm_token=confirm_token,
                         checks=lambda s, t: self.gate.check_start_mission(
                             s, self._last_mission_count),
                         execute=lambda: self.hub.send_mode("AUTO"),
                         readback="start the mission (AUTO)")

    @_tool
    def clear_pilot_override(self, confirm_token: Optional[str] = None) -> dict:
        """Hand control back to the LLM after the pilot took over (confirmation)."""
        o = self.gate.pilot_override()
        if not o:
            return {"ok": True, "note": "no pilot override active"}
        # Deliberately NOT gated by check_common's override check (that would
        # deadlock); still needs actuation enabled + confirmation.
        if not self.gate.cfg.actuation_enabled:
            return {"ok": False, "blocked": True, "reasons": ["actuation disabled"]}
        ok, why = self.gate.confirmations.check(confirm_token, "clear_pilot_override", {})
        if not ok:
            given = confirm_token is not None and confirm_token != ""
            self.gate.record_decision("clear_pilot_override", False, [why], stage="confirm")
            return self._ask_confirmation("clear_pilot_override", {},
                                          f"give control back to the LLM ({o['reason']})",
                                          bad_token=given, why=why)
        self.gate.clear_pilot_override(cause="operator confirmed clear_pilot_override")
        return {"ok": True, "note": "pilot override cleared; LLM actions allowed again"}


# Names grouped for the MCP layer / tests.
READ_ONLY_TOOLS = ["get_status", "get_telemetry", "get_data_flow", "get_recent_messages",
                   "get_params", "get_mission", "get_safety_limits", "get_gate_audit"]
SAFE_ACTION_TOOLS = ["hold", "brake", "land", "rtl"]
ACTION_TOOLS = ["arm", "disarm", "set_mode", "takeoff", "goto", "goto_relative",
                "set_speed", "set_param", "upload_mission", "start_mission",
                "clear_pilot_override"]
ALL_TOOLS = READ_ONLY_TOOLS + SAFE_ACTION_TOOLS + ACTION_TOOLS
