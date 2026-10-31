"""P1 #1: AUTO is not a bare mode switch. It is only reachable through
start_mission, which re-reads the mission stored on the FC, validates it and
needs a confirmation token bound to that exact mission."""
import pytest

from conftest import wait_for
from gate_helpers import HOME
from mavlink_hub import M
from safety_gate import FlightGate
from stack_config import SafetyConfig

FRAME = M.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
WP, RTL = M.MAV_CMD_NAV_WAYPOINT, M.MAV_CMD_NAV_RETURN_TO_LAUNCH


def item(seq, cmd=WP, dlat=0.0, dlon=0.0, alt=10.0, frame=FRAME, home=HOME):
    return (seq, cmd, int((home["lat"] + dlat) * 1e7), int((home["lon"] + dlon) * 1e7),
            alt, frame)


def stored(*body):
    return [(0, WP, 0, 0, 0.0, FRAME), *body]


# ------------------------------------------------------------------ gate (pure)
def dl(*rows):
    """Rows as MavlinkHub.download_mission() reports them."""
    names = {WP: "MAV_CMD_NAV_WAYPOINT", RTL: "MAV_CMD_NAV_RETURN_TO_LAUNCH"}
    return [{"seq": s, "command": names.get(c, f"CMD_{c}"), "lat": x / 1e7, "lon": y / 1e7,
             "alt_m": z, "frame": f} for s, c, x, y, z, f in rows]


def gate():
    return FlightGate(SafetyConfig(actuation_enabled=True, geofence_radius_m=60,
                                   max_alt_m=20, max_mission_items=3))


def test_default_allowed_modes_exclude_auto():
    assert "AUTO" not in SafetyConfig().allowed_modes
    assert not gate().check_mode("AUTO").allowed


def test_good_stored_mission_passes():
    rows = stored(item(1, dlat=0.0002), item(2, dlon=0.0002), item(3, RTL, alt=0))
    assert gate().check_stored_mission(dl(*rows), HOME).allowed


@pytest.mark.parametrize("rows,frag", [
    (stored(), "no mission"),
    (stored(item(1, dlat=0.01)), "geofence"),                       # ~1.1 km away
    (stored(item(1, alt=80)), "alt"),                               # above ceiling
    (stored(item(1, frame=M.MAV_FRAME_GLOBAL)), "relative-to-home"),
    (stored(item(1, 17)), "not allowed"),                           # NAV_LOITER_UNLIM
    (stored(item(1, 21)), "not allowed"),                           # NAV_LAND
    (stored(item(1), item(2), item(3), item(4), item(5)), "max"),   # too long
    (stored(item(1, RTL, alt=0)), "no NAV_WAYPOINT"),
])
def test_bad_stored_mission_is_refused(rows, frag):
    v = gate().check_stored_mission(dl(*rows), HOME)
    assert not v.allowed and frag in " ".join(v.reasons), v.reasons


def test_home_unknown_refuses_stored_mission():
    assert not gate().check_stored_mission(dl(*stored(item(1))), None).allowed


def test_digest_changes_with_the_mission():
    g = gate()
    a, b = dl(*stored(item(1))), dl(*stored(item(1, dlat=0.0001)))
    assert g.mission_digest(a) == g.mission_digest(list(a)) != g.mission_digest(b)


# ------------------------------------------------------------------ through the stack
def test_set_mode_auto_is_refused_by_default(airborne):
    t, fv = airborne
    r = t.set_mode("AUTO")
    assert r["blocked"] and "start_mission" in " ".join(r["reasons"])
    assert fv.mode == "GUIDED"
    assert not t.gate.confirmations.pending()


def test_start_mission_without_a_stored_mission_is_refused(airborne):
    t, fv = airborne
    r = t.start_mission()
    assert r["blocked"] and "no mission" in " ".join(r["reasons"])
    assert fv.mode == "GUIDED"


def test_start_mission_validates_then_needs_confirmation(airborne):
    t, fv = airborne
    fv.mission = stored(item(1, dlat=0.0002, home=fv_home(fv)), item(2, RTL, alt=0))
    first = t.start_mission()
    assert first["confirmation_required"] and fv.mode == "GUIDED"      # nothing sent yet
    assert "fingerprint" in first["readback"]
    r = t.start_mission(confirm_token=first["confirm_token"])
    assert r["ok"], r
    assert wait_for(lambda: fv.mode == "AUTO")


def test_stored_mission_outside_the_fence_is_refused_even_with_a_token(airborne):
    t, fv = airborne
    h = fv_home(fv)
    fv.mission = stored(item(1, dlat=0.0002, home=h))
    token = t.start_mission()["confirm_token"]
    fv.mission = stored(item(1, dlat=0.02, home=h))        # replaced behind our back
    r = t.start_mission(confirm_token=token)
    assert r["blocked"] and "geofence" in " ".join(r["reasons"])
    assert fv.mode == "GUIDED"


def test_token_is_bound_to_the_stored_mission(airborne):
    t, fv = airborne
    h = fv_home(fv)
    fv.mission = stored(item(1, dlat=0.0002, home=h))
    token = t.start_mission()["confirm_token"]
    fv.mission = stored(item(1, dlat=0.0003, home=h))      # different but still valid
    r = t.start_mission(confirm_token=token)
    assert r.get("confirmation_required") and fv.mode == "GUIDED"


def test_auto_in_allowed_modes_still_goes_through_start_mission(airborne):
    t, fv = airborne
    t.gate.cfg.allowed_modes = t.gate.cfg.allowed_modes + ["AUTO"]
    fv.mission = stored(item(1, dlat=0.0002, home=fv_home(fv)))
    r = t.set_mode("AUTO")
    assert r["confirmation_required"] and fv.mode == "GUIDED"
    fv.mission = stored(item(1, 17, home=fv_home(fv)))     # loiter-forever item
    assert t.set_mode("AUTO")["blocked"]


def fv_home(fv):
    return {"lat": fv.home[0], "lon": fv.home[1]}
