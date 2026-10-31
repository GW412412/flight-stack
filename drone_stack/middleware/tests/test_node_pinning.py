"""P1 #5: MavNode must pin the autopilot's sysid/compid BEFORE adopting any
state, and ignore (data, ACKs) every other source."""
from mav_helpers import M, attitude, bare_node, feed, heartbeat, make_msg, position, sys_status


def test_nothing_is_adopted_before_the_autopilot_is_pinned():
    n = bare_node()
    n._handle(position(lat=-35.0, sysid=9))
    n._handle(sys_status(battery=5, sysid=9))
    assert n.vehicle_sysid is None
    assert "lat" not in n.state and "position" not in n.telem
    assert n.state.get("battery") is None


def test_gcs_heartbeat_does_not_pin():
    n = bare_node()
    n._handle(heartbeat(sysid=255, compid=190, mtype=M.MAV_TYPE_GCS,
                        autopilot=M.MAV_AUTOPILOT_INVALID))
    assert n.vehicle_sysid is None


def test_other_sysid_and_other_compid_are_ignored_after_pin():
    n = bare_node()
    n._handle(heartbeat(sysid=1, compid=1))
    assert (n.vehicle_sysid, n.vehicle_compid) == (1, 1)
    n._handle(position(lat=-35.0, sysid=1, compid=1))
    n._handle(position(lat=10.0, sysid=2, compid=1))      # another system
    n._handle(position(lat=20.0, sysid=1, compid=100))    # another component
    n._handle(attitude(sysid=2))
    assert n.state["lat"] == -35.0
    assert "attitude" not in n.telem


def test_foreign_heartbeat_does_not_refresh_link_or_mode():
    n = bare_node()
    n._handle(heartbeat(sysid=1, mode=4))
    n._last_hb = 1.0                                      # pretend it is old
    n._handle(heartbeat(sysid=2, mode=5))
    assert n._last_hb == 1.0 and n.state["mode"] == "GUIDED"


def test_expected_sysid_refuses_to_pin_a_different_system():
    n = bare_node(expected_sysid=1)
    n._handle(heartbeat(sysid=7))
    assert n.vehicle_sysid is None
    n._handle(heartbeat(sysid=1))
    assert n.vehicle_sysid == 1


def test_waiters_only_see_the_pinned_vehicle():
    n = bare_node()
    w = n.subscribe(["COMMAND_ACK"])
    ack = lambda sysid: make_msg("command_ack", M.MAV_CMD_NAV_LAND, 0, sysid=sysid)  # noqa: E731
    # an ACK that arrives before pinning, one from a stranger, then the real one
    feed(n, [ack(3), heartbeat(sysid=1), ack(2), ack(1)])
    got = n.wait(w, 0.1)
    assert got is not None and got.get_srcSystem() == 1
    assert n.wait(w, 0.05) is None
