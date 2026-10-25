"""MavlinkHub: the ONE place that writes commands to the aircraft.

docs/INTEGRATION.md rule: one ground-station process is the sole COMMAND_LONG
writer. Everything that changes the vehicle - arm, mode, takeoff, guided
goto, speed, parameter writes, mission upload, and (legacy) the sprayer -
is transmitted from this class, and nowhere else. That gives us:

  * one audit log  -> logs/mavlink-commands.jsonl (one JSON line per write,
                      with the COMMAND_ACK result), per INTEGRATION.md §8
  * one dry-run switch (gate checks run, nothing is transmitted)
  * one lock, so two tool calls can never interleave a mission upload
  * pilot-override detection: the hub knows which mode IT asked for, so any
    other mode change must have come from the pilot's RC switch, QGC, or a
    flight-controller failsafe -> the FlightGate latch is set and the LLM
    backs off (only calming actions remain allowed).

The hub does NOT decide whether an action is allowed - the caller
(drone_tools.py) asks FlightGate first. The hub just transmits, waits for the
acknowledgement and reports what really happened.

Atomic check-and-send (2026-10-25): between "FlightGate said yes" and "the
packet left" there used to be a small window in which the pilot could take
over (the reader thread latches the override) and the LLM's command would
still go out. Now every vehicle-changing write goes through
`_gated_transmit`, which asks `FlightGate.send_if_permitted` to re-check pilot
authority and do the ONE short MAVLink write under the same lock the
override latch uses. Waiting for the COMMAND_ACK happens AFTER that lock is
released, so a takeover is never held up by more than one packet write.
DroneTools tells the hub which action it is performing (and whether it is a
calming "safe" action) with `with hub.acting_for(action, safe=...)`.
If the hub has no FlightGate (the legacy tools.py path) nothing changes.

Legacy spray API (request_spray_pulse / force_pump_off / spray_status) is
kept so drone_stack/middleware/tools.py keeps working; the LLM stack does
not expose it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, List, Optional, Tuple

os.environ.setdefault("MAVLINK20", "1")   # see mav_node.py
from pymavlink import mavutil  # noqa: E402

from safety_gate import GateRefused, SafetyGate

M = mavutil.mavlink
MAV_CMD_DO_SPRAYER = 216

# SET_POSITION_TARGET_GLOBAL_INT type_mask: use position only (ignore
# velocity, acceleration, yaw, yaw-rate).
POSITION_ONLY_MASK = 0b0000_1101_1111_1000   # 3576


@contextmanager
def _null_ctx() -> Iterator[None]:
    yield


def _result_name(result: int) -> str:
    try:
        return M.enums["MAV_RESULT"][result].name.replace("MAV_RESULT_", "")
    except KeyError:
        return str(result)


def _cmd_name(cmd: int) -> str:
    try:
        return M.enums["MAV_CMD"][cmd].name
    except KeyError:
        return str(cmd)


class MavlinkHub:
    """Single writer. One process should own one Hub per aircraft link."""

    def __init__(self, mav_node, gate: Optional[SafetyGate] = None, *,
                 flight_gate=None, dry_run: bool = False,
                 log_dir: Optional[str] = "logs", ack_timeout_s: float = 3.0):
        self.node = mav_node
        self.gate = gate or SafetyGate()          # legacy spray gate
        self.flight_gate = flight_gate            # FlightGate (pilot-override latch)
        self.dry_run = dry_run
        self.ack_timeout_s = ack_timeout_s
        self._lock = threading.RLock()
        self._pump_on = False
        self._last_cmd = None
        self._expected_mode: Optional[Tuple[str, float]] = None
        # Which DroneTools action the CURRENT thread is executing (see
        # acting_for). Thread-local so two MCP clients cannot mix them up.
        self._ctx = threading.local()
        self._log_path = None
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            self._log_path = os.path.join(log_dir, "mavlink-commands.jsonl")
        # Pilot-override detection hooks into the reader thread.
        if hasattr(mav_node, "add_mode_listener"):
            mav_node.add_mode_listener(self._on_mode_change)

    # ------------------------------------------------------------ plumbing
    @property
    def conn(self):
        return self.node.conn

    def _target(self) -> Tuple[int, int]:
        sysid = getattr(self.node, "vehicle_sysid", None) or self.conn.target_system or 1
        compid = getattr(self.node, "vehicle_compid", None) or self.conn.target_component or 1
        return sysid, compid

    def _log(self, entry: dict) -> None:
        entry = {"t": round(time.time(), 3), "dry_run": self.dry_run, **entry}
        self._last_cmd = entry
        if self._log_path:
            with open(self._log_path, "a") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")

    # ------------------------------------------------------------ authority
    @contextmanager
    def acting_for(self, action: str, *, safe: bool = False) -> Iterator[None]:
        """Tell the hub which gated action the writes inside this block
        belong to. DroneTools._act wraps every execute() in this.

        `safe=True` marks calming actions (LOITER/BRAKE/LAND/RTL/hold): their
        writes are never blocked by the pilot-override latch."""
        prev = (getattr(self._ctx, "action", None), getattr(self._ctx, "safe", False))
        self._ctx.action, self._ctx.safe = action, bool(safe)
        try:
            yield
        finally:
            self._ctx.action, self._ctx.safe = prev

    def _gate_telem(self) -> dict:
        """Fresh RC channels for the send-time inhibit-switch re-check.
        Sampled just BEFORE taking the gate lock so the lock is held only for
        the write itself. Never raises."""
        try:
            return self.node.get_telemetry() if hasattr(self.node, "get_telemetry") else {}
        except Exception:
            return {}

    def _gated_transmit(self, label: str, transmit: Callable[[], Any]) -> Any:
        """Run `transmit` (one short write, or a no-op in dry-run) as the
        final, atomic authority check. Raises GateRefused if the pilot took
        over since the tool's checks ran; nothing is sent in that case.

        Writes made outside `acting_for` (should not happen on the LLM path)
        are treated as gated, non-safe actions: fail closed."""
        if self.flight_gate is None:          # legacy tools.py path: unchanged
            return transmit()
        action = getattr(self._ctx, "action", None) or f"hub:{label}"
        safe = getattr(self._ctx, "safe", False)
        telem = None if safe else self._gate_telem()
        try:
            return self.flight_gate.send_if_permitted(action, transmit, safe=safe,
                                                      telem=telem)
        except GateRefused as exc:
            self._log({"kind": "REFUSED_AT_SEND", "label": label, "action": action,
                       "reasons": exc.reasons})
            raise

    def _send(self, fn_name: str, *args, force: bool = False,
              gated: bool = True) -> None:
        """Transmit one message under the node's send lock.

        force=True  : read-only request (REQUEST_MESSAGE, PARAM_REQUEST_READ);
                      sent even in dry-run and not gated (cannot move anything).
        gated=False : protocol continuation of a write that was already gated
                      at its commit point (mission items after MISSION_COUNT).
        otherwise   : the write is the commit point of an action -> it goes
                      through the FlightGate atomic check (and in dry-run the
                      check still runs but nothing is transmitted)."""
        def transmit() -> None:
            if self.dry_run and not force:
                return
            with self.node.send_lock:
                getattr(self.conn.mav, fn_name)(*args)

        if force or not gated:
            transmit()
        else:
            self._gated_transmit(fn_name, transmit)

    # ------------------------------------------------------------ COMMAND_LONG
    def send_command_long(self, command: int, p1: float = 0, p2: float = 0, p3: float = 0,
                          p4: float = 0, p5: float = 0, p6: float = 0, p7: float = 0, *,
                          label: str = "", wait_ack: bool = True,
                          timeout: Optional[float] = None, read_only: bool = False) -> dict:
        """THE COMMAND_LONG writer. Returns {'ok', 'ack', ...}.

        ok=True only if the FC answered MAV_RESULT_ACCEPTED (or, for
        dry-run, if it would have been sent). IN_PROGRESS counts as accepted
        for long-running commands such as takeoff.

        read_only=True marks pure requests (MAV_CMD_REQUEST_MESSAGE) that
        cannot move anything; they are transmitted even in dry-run so the
        dry-run server still sees home position etc."""
        with self._lock:
            sysid, compid = self._target()
            entry = {"kind": "COMMAND_LONG", "label": label, "command": _cmd_name(command),
                     "params": [p1, p2, p3, p4, p5, p6, p7], "target": f"{sysid}:{compid}"}
            if self.dry_run and not read_only:
                # Same send-time authority check as a real send, no packet.
                self._gated_transmit(entry["command"], lambda: None)
                self._log({**entry, "ack": "DRY_RUN"})
                return {"ok": True, "dry_run": True, "would_send": entry}
            w = self.node.subscribe(["COMMAND_ACK"], lambda m: m.command == command) \
                if wait_ack else None
            try:
                self._send("command_long_send", sysid, compid, command, 0,
                           p1, p2, p3, p4, p5, p6, p7, force=read_only)
                ack = self.node.wait(w, timeout or self.ack_timeout_s) if w else None
            finally:
                if w:
                    self.node.unsubscribe(w)
            if not wait_ack:
                self._log({**entry, "ack": "NOT_REQUESTED"})
                return {"ok": True, "sent": entry["command"]}
            if ack is None:
                self._log({**entry, "ack": "TIMEOUT"})
                return {"ok": False, "ack": "TIMEOUT", "command": entry["command"],
                        "note": "no COMMAND_ACK - check link; command may or may not have run"}
            res = _result_name(ack.result)
            self._log({**entry, "ack": res})
            return {"ok": res in ("ACCEPTED", "IN_PROGRESS"), "ack": res,
                    "command": entry["command"]}

    def request_message(self, msg_id: int) -> dict:
        """MAV_CMD_REQUEST_MESSAGE (read-only, e.g. HOME_POSITION=242)."""
        return self.send_command_long(M.MAV_CMD_REQUEST_MESSAGE, msg_id,
                                      label="request_message", timeout=2, read_only=True)

    # ------------------------------------------------------------ flight actions
    def send_mode(self, mode: str) -> dict:
        """MAV_CMD_DO_SET_MODE with ArduPilot custom mode number."""
        mode = mode.upper()
        mapping = self.node.mode_mapping() if hasattr(self.node, "mode_mapping") \
            else (self.conn.mode_mapping() or {})
        if mode not in mapping:
            return {"ok": False, "error": f"unknown mode {mode}", "available": sorted(mapping)}
        # Remember what WE asked for, so the reader does not mistake it for
        # the pilot moving the mode switch.
        self._expected_mode = (mode, time.monotonic())
        try:
            r = self.send_command_long(M.MAV_CMD_DO_SET_MODE,
                                       M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mapping[mode],
                                       label=f"set_mode {mode}")
        except GateRefused:
            # Nothing was sent, so do not expect this mode: if the PILOT now
            # selects it, that must still count as a pilot override.
            self._expected_mode = None
            raise
        r["requested_mode"] = mode
        return r

    def arm(self, arm: bool = True) -> dict:
        """Arm/disarm. Never uses the 21196 'force' magic number: the FC's
        own pre-arm checks always apply, and disarm in flight is refused."""
        return self.send_command_long(M.MAV_CMD_COMPONENT_ARM_DISARM, 1.0 if arm else 0.0,
                                      label="arm" if arm else "disarm", timeout=5)

    def takeoff(self, alt_m: float) -> dict:
        return self.send_command_long(M.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, float("nan"),
                                      0, 0, float(alt_m), label=f"takeoff {alt_m}m")

    def set_speed(self, speed_ms: float) -> dict:
        # param1 speed type 1 = ground speed; param3 -1 = throttle unchanged.
        return self.send_command_long(M.MAV_CMD_DO_CHANGE_SPEED, 1, float(speed_ms), -1,
                                      label=f"set_speed {speed_ms}m/s")

    def goto(self, lat: float, lon: float, alt_m: float) -> dict:
        """Guided-mode position target (relative-to-home altitude).

        SET_POSITION_TARGET_GLOBAL_INT has no ACK; success is judged by the
        caller watching the position (see mcp_server.goto)."""
        with self._lock:
            sysid, compid = self._target()
            entry = {"kind": "SET_POSITION_TARGET_GLOBAL_INT", "label": "goto",
                     "lat": lat, "lon": lon, "alt_m": alt_m, "target": f"{sysid}:{compid}"}
            self._send("set_position_target_global_int_send", 0, sysid, compid,
                       M.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, POSITION_ONLY_MASK,
                       int(lat * 1e7), int(lon * 1e7), float(alt_m),
                       0, 0, 0, 0, 0, 0, 0, 0)
            self._log({**entry, "ack": "NO_ACK_FOR_THIS_MESSAGE"})
            return {"ok": True, "dry_run": self.dry_run, "sent": entry}

    # ------------------------------------------------------------ parameters
    def get_param(self, name: str, timeout: float = 3.0) -> dict:
        name = name.upper()
        if self.dry_run and name in getattr(self.node, "params", {}):
            return {"ok": True, "name": name, "value": self.node.params[name], "cached": True}
        w = self.node.subscribe(["PARAM_VALUE"], lambda m: m.param_id == name)
        try:
            sysid, compid = self._target()
            with self.node.send_lock:   # read request: sent even in dry-run
                self.conn.mav.param_request_read_send(sysid, compid, name.encode(), -1)
            msg = self.node.wait(w, timeout)
        finally:
            self.node.unsubscribe(w)
        if msg is None:
            return {"ok": False, "name": name, "error": "no PARAM_VALUE reply (unknown name?)"}
        return {"ok": True, "name": name, "value": msg.param_value}

    def set_param(self, name: str, value: float, timeout: float = 3.0) -> dict:
        """PARAM_SET then read-back: ok only if the FC echoes the new value."""
        name = name.upper()
        with self._lock:
            entry = {"kind": "PARAM_SET", "label": "set_param", "name": name, "value": value}
            if self.dry_run:
                self._gated_transmit("param_set_send", lambda: None)
                self._log({**entry, "ack": "DRY_RUN"})
                return {"ok": True, "dry_run": True, "would_send": entry}
            before = self.node.params.get(name)
            if before is None:                      # read the old value first
                before = self.get_param(name).get("value")
            w = self.node.subscribe(["PARAM_VALUE"], lambda m: m.param_id == name)
            try:
                sysid, compid = self._target()
                self._send("param_set_send", sysid, compid, name.encode(), float(value),
                           M.MAV_PARAM_TYPE_REAL32)
                msg = self.node.wait(w, timeout)
            finally:
                self.node.unsubscribe(w)
            if msg is None:
                self._log({**entry, "ack": "TIMEOUT"})
                return {"ok": False, "name": name, "error": "no read-back"}
            ok = abs(msg.param_value - float(value)) < 1e-3 * max(1.0, abs(float(value)))
            self._log({**entry, "ack": "READBACK_OK" if ok else "READBACK_MISMATCH",
                       "readback": msg.param_value, "before": before})
            return {"ok": ok, "name": name, "value": msg.param_value, "previous": before}

    # ------------------------------------------------------------ missions
    def upload_mission(self, waypoints: List[dict], *, rtl_at_end: bool = True,
                       timeout: float = 5.0) -> dict:
        """Upload simple nav waypoints with the MAVLink mission protocol.

        Item 0 is the home placeholder (ArduPilot overwrites it); items 1..n
        are NAV_WAYPOINT in GLOBAL_RELATIVE_ALT_INT; optionally one RTL.
        Flow: MISSION_COUNT -> (MISSION_REQUEST_INT -> MISSION_ITEM_INT)* ->
        MISSION_ACK."""
        items = [(M.MAV_CMD_NAV_WAYPOINT, 0.0, 0.0, 0.0)]   # seq 0: home placeholder
        for wp in waypoints:
            items.append((M.MAV_CMD_NAV_WAYPOINT, float(wp["lat"]), float(wp["lon"]),
                          float(wp["alt_m"])))
        if rtl_at_end:
            items.append((M.MAV_CMD_NAV_RETURN_TO_LAUNCH, 0.0, 0.0, 0.0))
        with self._lock:
            entry = {"kind": "MISSION_UPLOAD", "label": "upload_mission",
                     "count": len(items), "waypoints": waypoints, "rtl_at_end": rtl_at_end}
            if self.dry_run:
                self._gated_transmit("mission_count_send", lambda: None)
                self._log({**entry, "ack": "DRY_RUN"})
                return {"ok": True, "dry_run": True, "would_upload": len(items)}
            sysid, compid = self._target()
            w = self.node.subscribe(["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"])
            try:
                self._send("mission_count_send", sysid, compid, len(items),
                           M.MAV_MISSION_TYPE_MISSION)
                sent = set()
                deadline = time.time() + timeout + 0.5 * len(items)
                while time.time() < deadline:
                    msg = self.node.wait(w, timeout)
                    if msg is None:
                        break
                    if msg.get_type() == "MISSION_ACK":
                        res = M.enums["MAV_MISSION_RESULT"][msg.type].name
                        ok = msg.type == M.MAV_MISSION_ACCEPTED and len(sent) == len(items)
                        self._log({**entry, "ack": res})
                        return {"ok": ok, "ack": res, "items_sent": len(sent),
                                "items_total": len(items)}
                    seq = msg.seq
                    if seq >= len(items):
                        continue
                    cmd, lat, lon, alt = items[seq]
                    # Continuation of the upload that was gated at
                    # MISSION_COUNT above. Not re-gated per item on purpose:
                    # aborting half-way would leave a partial mission on the
                    # FC. (Open question in DISCUSSION.md.)
                    self._send("mission_item_int_send", sysid, compid, seq,
                               M.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, cmd,
                               0, 1, 0, 0, 0, 0, int(lat * 1e7), int(lon * 1e7), alt,
                               M.MAV_MISSION_TYPE_MISSION, gated=False)
                    sent.add(seq)
            finally:
                self.node.unsubscribe(w)
            self._log({**entry, "ack": "TIMEOUT"})
            return {"ok": False, "ack": "TIMEOUT", "items_sent": len(sent),
                    "items_total": len(items)}

    def download_mission(self, timeout: float = 3.0) -> dict:
        """Read the mission currently on the FC (read-only)."""
        with self._lock:
            sysid, compid = self._target()
            w = self.node.subscribe(["MISSION_COUNT", "MISSION_ITEM_INT"])
            try:
                with self.node.send_lock:
                    self.conn.mav.mission_request_list_send(sysid, compid,
                                                            M.MAV_MISSION_TYPE_MISSION)
                msg = self.node.wait(w, timeout)
                while msg is not None and msg.get_type() != "MISSION_COUNT":
                    msg = self.node.wait(w, timeout)
                if msg is None:
                    return {"ok": False, "error": "no MISSION_COUNT reply"}
                count, items = msg.count, []
                for seq in range(count):
                    with self.node.send_lock:
                        self.conn.mav.mission_request_int_send(sysid, compid, seq,
                                                               M.MAV_MISSION_TYPE_MISSION)
                    it = self.node.wait(w, timeout)
                    while it is not None and (it.get_type() != "MISSION_ITEM_INT"
                                              or it.seq != seq):
                        it = self.node.wait(w, timeout)
                    if it is None:
                        return {"ok": False, "error": f"timeout on item {seq}", "items": items}
                    items.append({"seq": it.seq, "command": _cmd_name(it.command),
                                  "lat": it.x / 1e7, "lon": it.y / 1e7, "alt_m": it.z,
                                  "frame": it.frame})
                with self.node.send_lock:
                    self.conn.mav.mission_ack_send(sysid, compid, M.MAV_MISSION_ACCEPTED,
                                                   M.MAV_MISSION_TYPE_MISSION)
                return {"ok": True, "count": count, "items": items,
                        "note": "seq 0 is the home position"}
            finally:
                self.node.unsubscribe(w)

    # ------------------------------------------------------------ pilot authority
    def _on_mode_change(self, old: Optional[str], new: str) -> None:
        """Reader-thread callback: was this mode change ours?"""
        exp = self._expected_mode
        if exp and exp[0] == new and time.monotonic() - exp[1] < 5.0:
            return                       # we asked for it
        reason = f"mode {old}->{new} not requested by the LLM (RC switch, GCS or FC failsafe)"
        self._log({"kind": "PILOT_OVERRIDE_DETECTED", "label": reason})
        if self.flight_gate is not None:
            self.flight_gate.note_pilot_override(reason)

    # ------------------------------------------------------------ legacy spray
    def _send_sprayer(self, start: bool) -> None:
        # Pump OFF is a calming action: never blocked by the pilot-override
        # latch. Pump ON (legacy path) is gated like any other action when a
        # FlightGate is attached.
        ctx = self.acting_for("sprayer_stop", safe=True) if not start else _null_ctx()
        with ctx:
            self.send_command_long(MAV_CMD_DO_SPRAYER, 1.0 if start else 0.0,
                                   label="sprayer start" if start else "sprayer stop",
                                   wait_ack=False)
        self._pump_on = start

    def request_spray_pulse(self, *, conf: float, georeg: float, dose_ml: float,
                            zone_bounds: Optional[Tuple[float, float, float, float]] = None,
                            video_trusted: bool = True, live_id_enabled: bool = True,
                            on_time_s: float = 0.1) -> dict:
        """Legacy spray arbiter: gate check then brief DO_SPRAYER start/stop
        pulse. Not exposed to the LLM stack (spraying is out of scope)."""
        with self._lock:
            status = self.node.get_status()
            verdict = self.gate.can_spray(status, zone_bounds=zone_bounds, conf=conf,
                                          georeg=georeg, video_trusted=video_trusted,
                                          live_id_enabled=live_id_enabled)
            if not verdict.allowed:
                self._send_sprayer(False)
                return {"ok": False, "pump": "off", "blockers": verdict.reasons,
                        "dose_ml": dose_ml}
            t_on = max(0.04, min(0.40, float(on_time_s)))   # LIVE-PIPELINE bench window
            self._send_sprayer(True)
            time.sleep(t_on)
            self._send_sprayer(False)
            return {"ok": True, "pump": "pulsed", "on_time_s": t_on, "dose_ml": dose_ml,
                    "conf": conf, "georeg": georeg}

    def force_pump_off(self, reason: str = "fail-closed") -> dict:
        """Always allowed: stop sprayer. Call on video loss / gate fault."""
        with self._lock:
            self._send_sprayer(False)
            return {"ok": True, "pump": "off", "reason": reason}

    def spray_status(self) -> dict:
        return {"pump_on": self._pump_on, "last_cmd": self._last_cmd,
                "note": "Only MavlinkHub sends COMMAND_LONG (incl. MAV_CMD_DO_SPRAYER)"}
