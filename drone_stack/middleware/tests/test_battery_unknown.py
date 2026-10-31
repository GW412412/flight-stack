"""P1 #6: unknown battery is None and is handled safely (never 101, never a crash)."""
import pytest

from mav_helpers import bare_node, feed, heartbeat, sys_status
from safety_gate import SafetyGate, battery_unknown


def test_node_stores_unknown_battery_as_none():
    n = bare_node()
    assert n.state["battery"] is None              # before any SYS_STATUS
    feed(n, [heartbeat(), sys_status(battery=-1)])
    assert n.state["battery"] is None
    assert n.telem["battery"]["remaining_pct"] is None
    n._stop.clear()
    feed(n, [sys_status(battery=64)])
    assert n.state["battery"] == 64


@pytest.mark.parametrize("v,unknown", [(None, True), (-1, True), (float("nan"), True),
                                       (101, True), (True, True), ("50", True),
                                       (0, False), (55, False), (100.0, False)])
def test_battery_unknown_helper(v, unknown):
    assert battery_unknown(v) is unknown


def test_legacy_gates_do_not_crash_on_none_and_fail_closed():
    g = SafetyGate()
    v = g.can_arm({"mode": "GUIDED", "armed": False, "battery": None, "fix_type": 3})
    assert not v.allowed and "battery level unknown" in v.reasons
    # a status with no battery key at all is also unknown (the old default was 101)
    assert not g.can_arm({"mode": "GUIDED", "armed": False, "fix_type": 3}).allowed
    full = {"armed": True, "fix_type": 6, "rel_alt": 5, "battery": 80, "rc_link_ok": True,
            "in_geofence": True, "heartbeat_age_s": 0.2}
    assert g.can_spray(full).allowed
    for bad in (dict(full, battery=None), dict(full, rel_alt=None), dict(full, fix_type=None)):
        v = g.can_spray(bad)                      # must not raise TypeError
        assert not v.allowed
