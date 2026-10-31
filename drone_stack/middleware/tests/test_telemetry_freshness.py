"""P1 #2: per-message freshness (not just heartbeats) and no disarm when the
altitude is unknown unless a fresh landed state says ON_GROUND."""
from mav_helpers import M, attitude, bare_node, feed, heartbeat, make_msg, position, sys_status
from safety_gate import FlightGate
from stack_config import SafetyConfig
from gate_helpers import GOOD_TELEM, good_status


def gate(**kw):
    return FlightGate(SafetyConfig(actuation_enabled=True, min_action_interval_s=0,
                                   max_actions_per_minute=1000, **kw))


# ------------------------------------------------------------------ node
def test_node_tracks_each_message_type_separately():
    n = bare_node()
    assert all(v is None for v in n.msg_ages().values())
    feed(n, [heartbeat(), position(), sys_status()])
    ages = n.get_status()["msg_age_s"]
    assert ages["position"] is not None and ages["sys_status"] is not None
    assert ages["attitude"] is None and ages["gps"] is None and ages["ekf"] is None
    assert n.get_status()["link_ok"] is True        # heartbeat fresh, attitude never seen


def test_heartbeat_does_not_keep_other_streams_fresh():
    n = bare_node()
    feed(n, [heartbeat(), position(), attitude(), sys_status()])
    n._seen["position"] -= 30                       # position stream died 30 s ago
    n._stop.clear()
    feed(n, [heartbeat(), attitude(), sys_status()])
    st = n.get_status()
    assert st["link_ok"] and st["msg_age_s"]["position"] > 29
    g = gate()
    v = g.check_common("goto", st, GOOD_TELEM)
    assert not v.allowed and "position" in " ".join(v.reasons)


def test_foreign_messages_do_not_refresh_streams():
    n = bare_node()
    feed(n, [heartbeat(sysid=1), position(sysid=2), attitude(sysid=2, compid=100)])
    assert n.msg_ages()["position"] is None and n.msg_ages()["attitude"] is None


def test_landed_state_only_reported_while_fresh():
    n = bare_node()
    feed(n, [heartbeat(), make_msg("extended_sys_state", 0, M.MAV_LANDED_STATE_ON_GROUND)])
    assert n.get_status()["landed_state"] == "ON_GROUND"
    n._seen["landed"] -= 60
    assert n.get_status()["landed_state"] is None


# ------------------------------------------------------------------ gate
def test_every_non_safe_action_needs_fresh_position_attitude_sys_status():
    g = gate()
    assert g.check_common("set_speed", good_status(), GOOD_TELEM).allowed
    for stream in ("position", "attitude", "sys_status"):
        for bad in (None, 99.0):
            st = good_status()
            st["msg_age_s"][stream] = bad
            v = g.check_common("set_speed", st, GOOD_TELEM)
            assert not v.allowed and stream in " ".join(v.reasons), (stream, bad)
            # calming actions are not held up by this
            assert g.check_common("set_mode", st, GOOD_TELEM, safe=True).allowed


def test_status_without_an_age_table_fails_closed():
    st = good_status()
    del st["msg_age_s"]
    assert not gate().check_common("set_speed", st, GOOD_TELEM).allowed


def test_max_age_is_configurable():
    st = good_status()
    st["msg_age_s"]["attitude"] = 5.0
    assert not gate().check_common("set_speed", st, GOOD_TELEM).allowed
    assert gate(telemetry_max_age_s=10).check_common("set_speed", st, GOOD_TELEM).allowed


# ------------------------------------------------------------------ disarm
def test_disarm_refused_when_altitude_unknown():
    g = gate()
    v = g.check_disarm(good_status(armed=True, rel_alt=None))
    assert not v.allowed and "unknown" in " ".join(v.reasons)


def test_disarm_allowed_when_altitude_unknown_but_landed_confirmed():
    g = gate()
    assert g.check_disarm(good_status(armed=True, rel_alt=None, landed_state="ON_GROUND")).allowed


def test_disarm_refused_when_position_stale_even_if_last_alt_was_low():
    g = gate()
    st = good_status(armed=True, rel_alt=0.2)
    st["msg_age_s"]["position"] = 20.0              # last known 0.2 m, but 20 s old
    assert not g.check_disarm(st).allowed
    st["landed_state"] = "ON_GROUND"
    assert g.check_disarm(st).allowed


def test_disarm_refused_when_fc_says_in_air_even_at_low_altitude():
    g = gate()
    assert not g.check_disarm(good_status(armed=True, rel_alt=0.3, landed_state="IN_AIR")).allowed


def test_disarm_on_ground_with_known_altitude_still_works():
    assert gate().check_disarm(good_status(armed=True, rel_alt=0.1)).allowed


def test_start_mission_needs_known_altitude():
    g = gate()
    assert not g.check_start_mission(good_status(armed=True, rel_alt=None), 3).allowed
