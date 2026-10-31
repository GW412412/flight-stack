"""P1 #3: goto and mission commands need a 3D fix and a healthy EKF (fresh)."""
import pytest

from gate_helpers import GOOD_TELEM, good_status
from safety_gate import FlightGate
from stack_config import SafetyConfig

NAV = ("goto", "takeoff", "upload_mission", "start_mission")


def gate(**kw):
    return FlightGate(SafetyConfig(actuation_enabled=True, min_action_interval_s=0,
                                   max_actions_per_minute=1000, **kw))


@pytest.mark.parametrize("action", NAV)
def test_ready_vehicle_passes(action):
    assert gate().check_common(action, good_status(), GOOD_TELEM).allowed


@pytest.mark.parametrize("action", NAV)
@pytest.mark.parametrize("fix", [None, 0, 1, 2])
def test_no_3d_fix_blocks(action, fix):
    v = gate().check_common(action, good_status(fix_type=fix), GOOD_TELEM)
    assert not v.allowed and "GPS fix" in " ".join(v.reasons)


@pytest.mark.parametrize("action", NAV)
@pytest.mark.parametrize("telem", [{}, {"ekf": None},
                                   {"ekf": {"position_ok": False, "flag_names": ["attitude"]}}])
def test_missing_or_unhealthy_ekf_blocks(action, telem):
    v = gate().check_common(action, good_status(), telem)
    assert not v.allowed and "EKF" in " ".join(v.reasons)


@pytest.mark.parametrize("action", NAV)
@pytest.mark.parametrize("stream", ["gps", "ekf"])
def test_stale_gps_or_ekf_messages_block(action, stream):
    st = good_status()
    st["msg_age_s"][stream] = 30.0
    v = gate().check_common(action, st, GOOD_TELEM)
    assert not v.allowed and stream in " ".join(v.reasons)


@pytest.mark.parametrize("action", NAV)
def test_unknown_battery_blocks_navigation(action):
    v = gate().check_common(action, good_status(battery=None), GOOD_TELEM)
    assert not v.allowed and "battery" in " ".join(v.reasons)


def test_require_gps_fix_cannot_be_configured_below_3d():
    g = gate(require_gps_fix=0)
    assert not g.check_common("goto", good_status(fix_type=2), GOOD_TELEM).allowed


def test_higher_fix_requirement_is_honoured():
    g = gate(require_gps_fix=4)
    assert not g.check_common("goto", good_status(fix_type=3), GOOD_TELEM).allowed
    assert g.check_common("goto", good_status(fix_type=4), GOOD_TELEM).allowed


@pytest.mark.parametrize("action", ["set_speed", "set_mode", "disarm"])
def test_non_navigation_actions_are_not_held_to_nav_readiness(action):
    st = good_status(fix_type=0)
    assert gate().check_common(action, st, {}).allowed


def test_calming_actions_are_never_held_by_nav_readiness():
    g = gate()
    st = good_status(fix_type=0, battery=None)
    st["msg_age_s"].update(gps=None, ekf=None, position=None)
    assert g.check_common("set_mode", st, {}, safe=True).allowed
    assert g.check_common("hold", st, {}, safe=True).allowed
