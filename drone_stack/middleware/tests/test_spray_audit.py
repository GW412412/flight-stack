"""Spray audit trail, and the pilot's manual fire is never blocked by FlightGate.

Weeds / spray points: every sprayer command the stack sends is one line in
logs/mavlink-commands.jsonl. The pilot's manual fire button is a radio-to-FC
path that does not pass through the hub or either gate; these tests pin that
FlightGate has no say over the sprayer."""
import json

import pytest

from hub_helpers import StubNode
from mavlink_hub import MAV_CMD_DO_SPRAYER, MavlinkHub
from safety_gate import FlightGate
from stack_config import SafetyConfig


def read_log(tmp_path):
    return [json.loads(line) for line in
            (tmp_path / "mavlink-commands.jsonl").read_text().splitlines()]


def make_hub(tmp_path, flight_gate=None, dry_run=False):
    return MavlinkHub(StubNode(), flight_gate=flight_gate, dry_run=dry_run,
                      log_dir=str(tmp_path))


def sprayer_lines(rows):
    return [r["label"] for r in rows if r.get("command") == "MAV_CMD_DO_SPRAYER"]


def test_blocked_pulse_is_logged_with_reasons_and_pump_stays_off(tmp_path):
    hub = make_hub(tmp_path)                     # StubNode status is empty: not armed etc.
    r = hub.request_spray_pulse(conf=0.9, georeg=0.9, dose_ml=1.0)
    assert not r["ok"] and r["pump"] == "off"
    rows = read_log(tmp_path)
    decision = next(x for x in rows if x["kind"] == "SPRAY_DECISION")
    assert decision["allowed"] is False and decision["reasons"]
    assert sprayer_lines(rows) == ["sprayer stop"]


def test_allowed_pulse_logs_decision_start_and_stop(tmp_path):
    hub = make_hub(tmp_path)
    hub.node.status = {"armed": True, "fix_type": 6, "rel_alt": 5.0, "battery": 80,
                       "rc_link_ok": True, "heartbeat_age_s": 0.2, "video_trusted": True,
                       "lat": -37.0, "lon": 144.0}
    r = hub.request_spray_pulse(conf=0.9, georeg=0.9, dose_ml=1.0, on_time_s=0.05,
                                zone_bounds=(-38.0, 143.0, -36.0, 145.0))
    rows = read_log(tmp_path)
    assert r["ok"], r.get("blockers")
    assert [x["allowed"] for x in rows if x["kind"] == "SPRAY_DECISION"] == [True]
    assert sprayer_lines(rows) == ["sprayer start", "sprayer stop"]


def test_pump_off_is_logged(tmp_path):
    hub = make_hub(tmp_path)
    assert hub.force_pump_off("video lost")["ok"]
    assert sprayer_lines(read_log(tmp_path)) == ["sprayer stop"]


@pytest.fixture
def hostile_gate():
    """A FlightGate in the worst state: actuation off, pilot override latched."""
    g = FlightGate(SafetyConfig(actuation_enabled=False))
    g.note_pilot_override("test: pilot took the sticks")
    return g


def test_flight_gate_has_no_spray_action():
    g = FlightGate(SafetyConfig())
    assert not [n for n in dir(g) if "spray" in n.lower() or "pump" in n.lower()]
    assert not any("spray" in a for a in FlightGate.NAV_ACTIONS)


def test_flight_gate_state_does_not_touch_sprayer_commands(tmp_path, hostile_gate):
    """Actuation disabled + pilot-override latch must not stop DO_SPRAYER or pump-off
    from leaving the hub (the pilot may fire manually when armed, manual mode, RC up;
    FlightGate only governs the LLM's flight actions)."""
    hub = make_hub(tmp_path, flight_gate=hostile_gate)
    r = hub.send_command_long(MAV_CMD_DO_SPRAYER, 1.0, label="sprayer start", wait_ack=False)
    assert r["ok"]
    assert hub.force_pump_off()["ok"]
    sent = [c for c, _ in hub.node.sent]
    assert sent == [MAV_CMD_DO_SPRAYER, MAV_CMD_DO_SPRAYER]
    assert sprayer_lines(read_log(tmp_path)) == ["sprayer start", "sprayer stop"]


def test_dry_run_still_audits_sprayer_commands(tmp_path):
    hub = make_hub(tmp_path, dry_run=True)
    hub.force_pump_off()
    rows = read_log(tmp_path)
    assert rows[-1]["ack"] == "DRY_RUN" and not hub.node.sent
