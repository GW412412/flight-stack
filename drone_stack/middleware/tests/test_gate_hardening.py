"""Tests for the FlightGate hardening of 2026-10-25.

What is covered, in plain words:

* atomic check-and-send: a pilot takeover can never land between "the gate
  said yes" and "the packet left" (thread race tests, gate and hub level);
* confirmation-code lifetime (TTL): capped, configurable, expired codes refused;
* the gate's own monotonic clock: wall-clock jumps change nothing;
* bounded replay memory: a used code is never accepted twice, never
  re-issued, and the memory cannot grow without limit;
* bounded audit trail of decisions and authority-state changes, read-only;
* malformed input (wrong types, NaN, infinity) is refused, never raises.

Design ideas credited to Claude (Anthropic, claude.ai chat), adapted to this
code base. No network, no SITL: the hub is driven with a tiny in-process fake
node so the tests are fast and deterministic.
"""
import json
import math
import threading
import time
from types import SimpleNamespace

import pytest

import safety_gate
from drone_tools import DroneTools
from mavlink_hub import MavlinkHub
from safety_gate import (MAX_CONFIRM_TTL_S, REPLAY_MEMORY_MAX, REPLAY_MEMORY_S,
                         ConfirmationBook, FlightGate, GateAudit, GateRefused,
                         finite_number, validate_confirm_ttl)
from stack_config import SafetyConfig, load_config

HOME = {"lat": -35.363261, "lon": 149.165230, "alt_msl_m": 584.0}
GOOD_TELEM = {"ekf": {"position_ok": True, "flag_names": []}, "rc": {"channels": [1500] * 8}}
BAD_VALUES = [None, "10", True, float("nan"), float("inf"), -float("inf"), [1], {"x": 1}]


class Clock:
    """Manually advanced clock so TTL tests are instant."""
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def cfg(**kw):
    base = dict(actuation_enabled=True, max_alt_m=30, geofence_radius_m=150,
                max_speed_ms=6, require_rc_link=True, min_action_interval_s=0,
                max_actions_per_minute=1000)
    base.update(kw)
    return SafetyConfig(**base)


# =============================================================== fake I/O
class FakeMav:
    """Records every MAVLink write. `on_write` lets a test act mid-write."""
    def __init__(self):
        self.writes = []
        self.on_write = None

    def __getattr__(self, name):
        if not name.endswith("_send"):
            raise AttributeError(name)

        def send(*args):
            if self.on_write:
                self.on_write(name, args)
            self.writes.append(name)
        return send


class FakeNode:
    """Just enough of MavNode for MavlinkHub + DroneTools, with no sockets.
    Every COMMAND_ACK wait answers ACCEPTED immediately."""
    def __init__(self, **status):
        self.send_lock = threading.Lock()
        self.conn = SimpleNamespace(mav=FakeMav(), target_system=1, target_component=1)
        self.vehicle_sysid, self.vehicle_compid = 1, 1
        self.params = {}
        self.status = {"link_ok": True, "heartbeat_age_s": 0.2, "mode": "GUIDED",
                       "armed": True, "fix_type": 3, "battery": 90, "rc_link_ok": True,
                       "rel_alt": 6.0, "lat": HOME["lat"], "lon": HOME["lon"]}
        self.status.update(status)
        self.listeners = []
        self.state = self.status

    def add_mode_listener(self, cb):
        self.listeners.append(cb)

    def mode_mapping(self):
        return {"GUIDED": 4, "LOITER": 5, "RTL": 6, "LAND": 9, "AUTO": 3, "BRAKE": 17}

    def get_status(self):
        return dict(self.status)

    def get_telemetry(self):
        return dict(GOOD_TELEM)

    def home(self):
        return dict(HOME)

    def link_ok(self):
        return True

    def subscribe(self, types, pred=None):
        return object()

    def wait(self, w, timeout):
        return SimpleNamespace(result=0, command=0)      # MAV_RESULT_ACCEPTED

    def unsubscribe(self, w):
        pass

    def pilot_switches_mode(self, new):
        """Simulate the reader thread seeing a mode change from the RC radio."""
        old, self.status["mode"] = self.status["mode"], new
        for cb in self.listeners:
            cb(old, new)


def stack(dry_run=False, clock=None, **gate_kw):
    node = FakeNode()
    gate = FlightGate(cfg(**gate_kw), clock=clock) if clock else FlightGate(cfg(**gate_kw))
    hub = MavlinkHub(node, flight_gate=gate, dry_run=dry_run, log_dir=None)
    tools = DroneTools(node, hub, gate, log_dir=None, home_poll_s=0)
    return tools, node, gate, hub


# =============================================================== 1. atomic send
def test_takeover_race_gate_level_latch_waits_for_send():
    """The pilot thread tries to latch an override while a gated send is in
    its window (permit given, bytes not yet out). With the authority lock the
    latch must wait; without it, the latch lands inside the window and this
    test fails. (Verified by temporarily removing the lock.)"""
    g = FlightGate(cfg())
    violations = []
    for i in range(10):
        g.clear_pilot_override()
        in_send = threading.Event()

        def send():
            in_send.set()
            time.sleep(0.03)                    # the race window
            if g._override is not None:         # "transmit" happens here
                violations.append(i)
            return "sent"

        def pilot():
            in_send.wait(2)
            g.note_pilot_override("mode GUIDED->LOITER not requested (RC switch)")

        t = threading.Thread(target=pilot)
        t.start()
        assert g.send_if_permitted("goto", send, telem=GOOD_TELEM) == "sent"
        t.join(2)
        # the latch did land - just after the send - and now blocks the next one
        assert g.pilot_override() is not None
        with pytest.raises(GateRefused):
            g.send_if_permitted("goto", lambda: pytest.fail("must not send"), telem=GOOD_TELEM)
    assert violations == []


def test_takeover_race_hub_level_through_real_tool_call():
    """Same race through DroneTools -> MavlinkHub -> FakeMav: the RC mode
    change arrives on another thread while the hub is writing goto."""
    tools, node, gate, hub = stack()
    seen_override_at_write = []
    in_write = threading.Event()

    def on_write(name, args):
        if name == "set_position_target_global_int_send":
            in_write.set()
            time.sleep(0.05)
            seen_override_at_write.append(gate._override is not None)
    node.conn.mav.on_write = on_write

    pilot = threading.Thread(target=lambda: (in_write.wait(2),
                                             node.pilot_switches_mode("LOITER")))
    pilot.start()
    r = tools.goto(HOME["lat"] + 0.0001, HOME["lon"], 10)
    pilot.join(2)
    assert r["ok"], r
    assert seen_override_at_write == [False]
    assert gate.pilot_override() is not None
    # The next gated action is refused; a calming action still goes out.
    node.conn.mav.on_write = None
    node.status["mode"] = "GUIDED"
    assert tools.goto(HOME["lat"], HOME["lon"], 10)["blocked"]
    assert tools.land()["ok"]


def test_override_between_check_and_send_blocks_the_write(monkeypatch):
    """The pilot takes over AFTER check_common passed but BEFORE the write:
    the send-time re-check refuses and nothing is transmitted."""
    tools, node, gate, hub = stack()
    real = gate.check_speed

    def check_then_pilot_moves(speed):
        v = real(speed)
        node.pilot_switches_mode("LOITER")      # lands in the old gap
        return v
    monkeypatch.setattr(gate, "check_speed", check_then_pilot_moves)
    r = tools.set_speed(3)
    assert r["ok"] is False and r["blocked"] and r["stage"] == "send"
    assert any("pilot override" in x for x in r["reasons"])
    assert node.conn.mav.writes == []
    last = gate.audit.entries()[-1]
    assert last["event"] == "send" and last["allowed"] is False
    assert last["state"] == "pilot_override"


def test_dry_run_also_rechecks_at_send(monkeypatch):
    """In dry-run 'would_send' is only reported if the send-time check passes."""
    tools, node, gate, hub = stack(dry_run=True)
    assert tools.set_speed(3)["ok"]                  # normal dry run
    real = gate.check_speed
    monkeypatch.setattr(gate, "check_speed",
                        lambda s: (node.pilot_switches_mode("LOITER"), real(s))[1])
    r = tools.set_speed(3)
    assert r["blocked"] and r["stage"] == "send" and "would_send" not in r
    assert node.conn.mav.writes == []


def test_safe_actions_never_blocked_by_override_at_send():
    tools, node, gate, hub = stack()
    gate.note_pilot_override("pilot flipped to LOITER")
    for fn in (tools.land, tools.rtl, tools.brake):
        assert fn()["ok"]
    assert node.conn.mav.writes.count("command_long_send") == 3


def test_refused_mode_send_does_not_mask_a_later_pilot_mode_change(monkeypatch):
    tools, node, gate, hub = stack()
    real = gate.check_mode
    monkeypatch.setattr(gate, "check_mode",
                        lambda m: (gate.note_pilot_override("x"), real(m))[1])
    node.status["mode"] = "LOITER"
    assert tools.set_mode("AUTO")["stage"] == "send"
    assert hub._expected_mode is None
    gate.clear_pilot_override()
    node.pilot_switches_mode("AUTO")                 # pilot really selects AUTO
    assert gate.pilot_override() is not None


def test_write_outside_acting_for_fails_closed():
    tools, node, gate, hub = stack()
    gate.note_pilot_override("pilot")
    with pytest.raises(GateRefused):
        hub.set_speed(3)                            # not via DroneTools
    assert node.conn.mav.writes == []


def test_legacy_hub_without_flight_gate_unchanged():
    node = FakeNode()
    hub = MavlinkHub(node, dry_run=False, log_dir=None)
    assert hub.set_speed(3)["ok"]
    assert node.conn.mav.writes == ["command_long_send"]


def test_send_failure_is_audited_and_reraised_without_latching():
    g = FlightGate(cfg())

    def boom():
        raise OSError("serial port gone")
    with pytest.raises(OSError):
        g.send_if_permitted("goto", boom, telem=GOOD_TELEM)
    last = g.audit.entries()[-1]
    assert last["sent"] is False and "serial port gone" in last["reasons"][0]
    assert g.authority_state() == "llm_permitted"     # open question: no fault latch


def test_safe_send_still_refused_when_read_only():
    g = FlightGate(cfg(actuation_enabled=False))
    with pytest.raises(GateRefused):
        g.send_if_permitted("set_mode", lambda: None, safe=True)


def test_rc_inhibit_rechecked_at_send():
    g = FlightGate(cfg(llm_inhibit_rc_channel=7))
    telem = {"rc": {"channels": [1500] * 6 + [1900, 1500]}}
    with pytest.raises(GateRefused):
        g.send_if_permitted("goto", lambda: pytest.fail("sent"), telem=telem)


# =============================================================== 2. TTL
def test_code_valid_up_to_ttl_then_expired_with_reason():
    clk = Clock()
    book = ConfirmationBook(ttl_s=45, clock=clk)
    tok = book.request("arm", {}, "ARM")
    clk.t += 45
    assert book.check(tok, "arm", {}) == (True, "ok")
    tok2 = book.request("arm", {}, "ARM")
    clk.t += 45.01
    ok, why = book.check(tok2, "arm", {})
    assert not ok and "expired" in why


@pytest.mark.parametrize("bad", [0, -1, MAX_CONFIRM_TTL_S + 0.1, 1e9, math.nan,
                                 math.inf, True, "45", None])
def test_invalid_ttl_refused_at_startup(bad):
    with pytest.raises(ValueError):
        validate_confirm_ttl(bad)
    with pytest.raises(ValueError):
        FlightGate(cfg(confirm_ttl_s=bad))


def test_ttl_cap_accepts_cap_and_default_is_placeholder_value():
    assert validate_confirm_ttl(MAX_CONFIRM_TTL_S) == MAX_CONFIRM_TTL_S
    assert SafetyConfig().confirm_ttl_s == safety_gate.DEFAULT_CONFIRM_TTL_S == 45.0


def test_shipped_profiles_set_a_ttl_within_cap():
    import os
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config")
    for name in ("llm_stack.sitl.toml", "llm_stack.pixhawk.toml"):
        c = load_config(os.path.join(root, name))
        assert 0 < c.safety.confirm_ttl_s <= MAX_CONFIRM_TTL_S
        assert c.safety.confirm_ttl_s == 45


def test_load_config_refuses_huge_ttl(tmp_path):
    p = tmp_path / "x.toml"
    p.write_text("[safety]\nconfirm_ttl_s = 4500\n")
    with pytest.raises(ValueError):
        load_config(str(p))


def test_expired_code_refused_through_the_tool_and_nothing_sent():
    clk = Clock()
    tools, node, gate, hub = stack(clock=clk)
    node.status.update(armed=False, rel_alt=0.0)
    r = tools.arm()
    assert r["confirmation_required"] and r["expires_in_s"] == 45
    clk.t += 46
    r2 = tools.arm(confirm_token=r["confirm_token"])
    assert r2["ok"] is False and "expired" in r2["warning"]
    assert node.conn.mav.writes == []
    assert tools.arm(confirm_token=r2["confirm_token"])["ok"]   # fresh code works


# =============================================================== 3. clock
def test_gate_uses_monotonic_clock_by_default():
    g = FlightGate(cfg())
    assert g._clock is time.monotonic
    assert g.confirmations._clock is time.monotonic
    assert g.rate._clock is time.monotonic


def test_wall_clock_jump_does_not_expire_or_extend_codes(monkeypatch):
    g = FlightGate(cfg())
    tok = g.confirmations.request("arm", {}, "ARM")
    monkeypatch.setattr(time, "time", lambda: 4e9)    # NTP/GPS jump forwards
    assert g.confirmations.consume(tok, "arm", {})


def test_no_gate_method_takes_a_caller_time():
    import inspect
    for name in ("send_if_permitted", "check_common", "pilot_override",
                 "note_pilot_override"):
        params = inspect.signature(getattr(FlightGate, name)).parameters
        assert not {"now", "t", "time", "timestamp"} & set(params), name
    for name in ("request", "check", "consume"):
        params = inspect.signature(getattr(ConfirmationBook, name)).parameters
        assert "now" not in params


def test_override_hold_time_uses_gate_clock():
    clk = Clock()
    g = FlightGate(cfg(pilot_override_hold_s=10), clock=clk)
    g.note_pilot_override("x")
    assert g.pilot_override()["age_s"] == 0
    clk.t += 10.5
    assert g.pilot_override() is None
    assert g.audit.entries()[-1]["event"] == "override_expired"


# =============================================================== 4. replay
def test_used_code_refused_with_clear_reason():
    book = ConfirmationBook(clock=Clock())
    tok = book.request("takeoff", {"alt_m": 10.0}, "TAKE OFF")
    assert book.check(tok, "takeoff", {"alt_m": 10.0})[0]
    ok, why = book.check(tok, "takeoff", {"alt_m": 10.0})
    assert not ok and "already used" in why


def test_wrong_args_do_not_burn_but_do_not_pass():
    book = ConfirmationBook(clock=Clock())
    tok = book.request("takeoff", {"alt_m": 10.0}, "TAKE OFF")
    ok, why = book.check(tok, "takeoff", {"alt_m": 50.0})
    assert not ok and "different" in why
    assert book.check(tok, "takeoff", {"alt_m": 10.0})[0]


def test_used_code_is_never_reissued(monkeypatch):
    seq = iter(["abc123", "abc123", "abc123", "def456"])
    monkeypatch.setattr(safety_gate.secrets, "token_hex", lambda n: next(seq))
    book = ConfirmationBook(clock=Clock())
    t1 = book.request("arm", {}, "ARM")
    assert book.consume(t1, "arm", {})
    t2 = book.request("arm", {}, "ARM")
    assert t1 == "ABC123" and t2 == "DEF456"


def test_replay_memory_bounded_by_count_and_age():
    clk = Clock()
    book = ConfirmationBook(clock=clk)
    for _ in range(REPLAY_MEMORY_MAX + 50):
        tok = book.request("arm", {}, "ARM")
        assert book.consume(tok, "arm", {})
    assert book.replay_memory_size() == REPLAY_MEMORY_MAX
    clk.t += REPLAY_MEMORY_S + 1
    assert book.replay_memory_size() == 0


def test_pending_codes_are_capped_oldest_dropped():
    clk = Clock()
    book = ConfirmationBook(clock=clk)
    first = book.request("arm", {}, "ARM")
    for i in range(safety_gate.MAX_PENDING_CONFIRMATIONS + 3):
        clk.t += 0.01
        book.request("set_speed", {"speed_ms": i}, "x")
    assert len(book.pending()) == safety_gate.MAX_PENDING_CONFIRMATIONS
    ok, why = book.check(first, "arm", {})
    assert not ok and "dropped" in why


@pytest.mark.parametrize("tok", [123, 1.5, [], {"a": 1}, b"ABCDEF", True, "ZZZZZZ",
                                 "12345", "1234567", "  ", object()])
def test_malformed_codes_refused_never_raise(tok):
    book = ConfirmationBook(clock=Clock())
    book.request("arm", {}, "ARM")
    ok, why = book.check(tok, "arm", {})
    assert ok is False and isinstance(why, str)


# =============================================================== 5. audit
def test_audit_records_state_transitions_with_cause():
    g = FlightGate(cfg())
    g.note_pilot_override("mode GUIDED->STABILIZE not requested by the LLM")
    g.clear_pilot_override()
    latched, cleared = g.audit.entries()[-2:]
    assert latched["event"] == "override_latched"
    assert (latched["prev_state"], latched["state"]) == ("llm_permitted", "pilot_override")
    assert "STABILIZE" in latched["cause"]
    assert (cleared["prev_state"], cleared["state"]) == ("pilot_override", "llm_permitted")


def test_audit_is_bounded_and_ordered():
    a = GateAudit(max_entries=3, clock=Clock())
    for i in range(5):
        a.record("x", prev_state="s", state="s", cause=str(i))
    assert [e["seq"] for e in a.entries()] == [3, 4, 5]
    assert len(a.entries(limit=1)) == 1
    for bad in BAD_VALUES:
        assert isinstance(a.entries(limit=bad), list)       # never raises


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "10"])
def test_audit_capacity_validated(bad):
    with pytest.raises(ValueError):
        GateAudit(max_entries=bad)


def test_tool_audit_trail_is_readable_and_never_contains_codes():
    tools, node, gate, hub = stack()
    node.status.update(armed=False, rel_alt=0.0)
    r = tools.arm()
    tok = r["confirm_token"]
    tools.arm(confirm_token="000000")                   # wrong code
    tools.takeoff(999)                                  # refused by envelope
    out = tools.get_gate_audit(limit=100)
    assert out["ok"] and out["count"] >= 3
    events = [(e["event"], e["cause"], e["allowed"]) for e in out["entries"]]
    assert ("decision", "confirm", False) in events
    assert ("decision", "check", False) in events
    blob = json.dumps(out)
    assert tok not in blob and "000000" not in blob
    # the tool only reads
    before = len(gate.audit.entries())
    tools.get_gate_audit()
    assert len(gate.audit.entries()) == before


def test_get_safety_limits_reports_ttl_and_state():
    tools, *_ = stack()
    d = tools.get_safety_limits()
    assert d["confirm_ttl_s"] == 45 and d["confirm_ttl_max_s"] == MAX_CONFIRM_TTL_S
    assert d["authority_state"] == "llm_permitted"


# =============================================================== 6. malformed
def test_finite_number_accepts_numpy_like_reals():
    from fractions import Fraction
    assert finite_number(Fraction(1, 2), "x") == (0.5, None)
    assert finite_number(3, "x") == (3.0, None)
    np = pytest.importorskip("numpy")
    assert finite_number(np.float64(2.5), "x") == (2.5, None)
    assert finite_number(np.int64(3), "x") == (3.0, None)
    assert finite_number(np.float64("nan"), "x")[0] is None


@pytest.mark.parametrize("bad", BAD_VALUES)
def test_finite_number_refuses_cleanly(bad):
    v, why = finite_number(bad, "x")
    assert v is None and why


@pytest.mark.parametrize("bad", BAD_VALUES)
def test_gate_checks_refuse_malformed(bad):
    g = FlightGate(cfg())
    assert not g.check_takeoff(bad, {"armed": True, "mode": "GUIDED"}).allowed
    assert not g.check_speed(bad).allowed
    assert not g.check_param("WP_SPD", bad).allowed
    assert not g.check_param(bad, 1.0).allowed
    assert not g.check_goto(bad, HOME["lon"], 10, {"armed": True, "mode": "GUIDED"},
                            HOME).allowed
    assert not g.check_mission([{"lat": HOME["lat"], "lon": bad, "alt_m": 10}], HOME).allowed
    if not isinstance(bad, str):
        assert not g.check_mode(bad).allowed
        assert not g.is_safe_mode(bad)


def test_gate_mission_shape_errors_refused():
    g = FlightGate(cfg())
    assert not g.check_mission("abc", HOME).allowed
    assert not g.check_mission(["x", {"lat": 1}], HOME).allowed


def test_rc_inhibit_garbage_channel_fails_closed():
    g = FlightGate(cfg(llm_inhibit_rc_channel=2))
    assert g.rc_inhibit_active({"rc": {"channels": [1500, None]}})
    assert g.rc_inhibit_active({"rc": {"channels": [1500, float("nan")]}})
    assert g.rc_inhibit_active({"rc": "nonsense"})            # garbled = ON
    assert g.rc_inhibit_active({"rc": {"channels": "1500,1900"}})
    assert not g.rc_inhibit_active({})                        # missing = OFF, as before
    assert not g.rc_inhibit_active({"rc": {"channels": [1500]}})


@pytest.mark.parametrize("bad", BAD_VALUES)
def test_tools_refuse_malformed_and_never_raise(bad):
    """Every tool returns a dict for every bad value, and no movement or
    write tool transmits anything. (None is a legal 'not given' for wait_s
    and goto_relative's alt_m, so those two are only checked for not raising.)"""
    tools, node, gate, hub = stack()
    must_refuse = [
        lambda: tools.takeoff(bad),
        lambda: tools.goto(bad, HOME["lon"], 10),
        lambda: tools.goto_relative(bad, 0),
        lambda: tools.set_speed(bad),
        lambda: tools.set_param("WP_SPD", bad),
        lambda: tools.set_param(bad, 1.0),
        lambda: tools.set_mode(bad),
        lambda: tools.upload_mission(bad),
        lambda: tools.upload_mission([{"lat": bad, "lon": HOME["lon"], "alt_m": 10}]),
        lambda: tools.upload_mission([bad]),
        lambda: tools.start_mission(confirm_token=bad),
    ]
    if bad is not None:
        must_refuse += [
            lambda: tools.goto(HOME["lat"], HOME["lon"], 10, wait_s=bad),
            lambda: tools.goto_relative(0, 0, alt_m=bad),
        ]
    for call in must_refuse:
        r = call()
        assert isinstance(r, dict) and r.get("ok") is False, r
    assert node.conn.mav.writes == []
    node.status.update(armed=False, rel_alt=0.0)
    for call in (lambda: tools.arm(confirm_token=bad),
                 lambda: tools.clear_pilot_override(confirm_token=bad),
                 lambda: tools.get_recent_messages(limit=bad),
                 lambda: tools.get_params(bad),
                 lambda: tools.get_gate_audit(limit=bad)):
        assert isinstance(call(), dict)
    assert node.conn.mav.writes == []      # a bad code never arms


def test_string_numbers_are_refused_not_coerced():
    """'10' is refused (the MCP layer already delivers floats); this is a
    deliberate tightening: guessing a unit or a value is not the gate's job."""
    tools, node, gate, hub = stack()
    r = tools.set_speed("3")
    assert r["blocked"] and "must be a number" in r["reasons"][0]
    assert node.conn.mav.writes == []


def test_nan_coordinates_never_reach_the_wire():
    tools, node, gate, hub = stack()
    for args in ((math.nan, HOME["lon"], 10), (HOME["lat"], math.inf, 10),
                 (HOME["lat"], HOME["lon"], math.nan)):
        r = tools.goto(*args)
        assert r["ok"] is False and r["blocked"]
    assert node.conn.mav.writes == []
