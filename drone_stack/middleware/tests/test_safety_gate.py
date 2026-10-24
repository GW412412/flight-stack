"""Unit tests for safety_gate.FlightGate - the deterministic checks that sit
between the LLM and the aircraft. No MAVLink, no network."""
import pytest

from safety_gate import ConfirmationBook, FlightGate, RateLimiter, SafetyGate
from stack_config import SafetyConfig

HOME = {"lat": -35.363261, "lon": 149.165230, "alt_msl_m": 584.0}


class Clock:
    """Manually advanced clock so TTL / rate tests are instant."""
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


def good_status(**kw):
    s = {"link_ok": True, "heartbeat_age_s": 0.5, "mode": "GUIDED", "armed": False,
         "fix_type": 3, "battery": 90, "rc_link_ok": True, "rel_alt": 0.0,
         "lat": HOME["lat"], "lon": HOME["lon"]}
    s.update(kw)
    return s


GOOD_TELEM = {"ekf": {"position_ok": True, "flag_names": []}, "rc": {"channels": [1500] * 8}}


# ------------------------------------------------------------ common checks
def test_actuation_disabled_blocks_everything_but_reports_why():
    g = FlightGate(cfg(actuation_enabled=False))
    v = g.check_common("arm", good_status(), GOOD_TELEM)
    assert not v.allowed and "actuation disabled" in v.reasons[0]
    # even safe actions: a read-only server sends nothing at all
    assert not g.check_common("set_mode", good_status(), GOOD_TELEM, safe=True).allowed


def test_stale_link_blocks():
    g = FlightGate(cfg())
    v = g.check_common("goto", good_status(link_ok=False, heartbeat_age_s=9), GOOD_TELEM)
    assert not v.allowed and "link" in v.reasons[0]


def test_pilot_override_blocks_actions_but_not_safe_modes():
    g = FlightGate(cfg())
    g.note_pilot_override("mode GUIDED->LOITER not requested")
    assert not g.check_common("goto", good_status(), GOOD_TELEM).allowed
    assert g.check_common("set_mode", good_status(), GOOD_TELEM, safe=True).allowed
    g.clear_pilot_override()
    assert g.check_common("goto", good_status(), GOOD_TELEM).allowed


def test_pilot_override_auto_expiry():
    clk = Clock()
    g = FlightGate(cfg(pilot_override_hold_s=10), clock=clk)
    g.note_pilot_override("x")
    assert g.pilot_override()
    clk.t += 11
    assert g.pilot_override() is None


def test_rc_inhibit_switch():
    g = FlightGate(cfg(llm_inhibit_rc_channel=7, llm_inhibit_pwm_above=1800))
    hi = {"rc": {"channels": [1500] * 6 + [2000, 1500]}}
    lo = {"rc": {"channels": [1500] * 8}}
    assert not g.check_common("goto", good_status(), hi).allowed
    assert g.check_common("goto", good_status(), lo).allowed
    assert g.check_common("set_mode", good_status(), hi, safe=True).allowed


def test_rate_limiter_spacing_and_per_minute():
    clk = Clock(0.0)
    r = RateLimiter(min_interval_s=2, per_minute=3, clock=clk)
    assert r.check() is None
    r.record()
    clk.t = 1.0
    assert r.check()            # too soon
    for t in (3.0, 6.0):
        clk.t = t
        assert r.check() is None
        r.record()
    clk.t = 9.0
    assert "minute" in r.check()   # 3 in the last 60 s
    clk.t = 70.0
    assert r.check() is None


# ------------------------------------------------------------ confirmation
def test_confirmation_single_use_and_bound_to_args():
    book = ConfirmationBook(ttl_s=60, clock=Clock())
    tok = book.request("takeoff", {"alt_m": 10}, "TAKE OFF to 10 m")
    assert not book.consume(tok, "takeoff", {"alt_m": 50})   # different args
    assert not book.consume(tok, "arm", {"alt_m": 10})       # different action
    assert book.consume(tok.lower(), "takeoff", {"alt_m": 10, "confirm_token": tok})
    assert not book.consume(tok, "takeoff", {"alt_m": 10})   # single use
    assert not book.consume(None, "takeoff", {"alt_m": 10})


def test_confirmation_expires():
    clk = Clock()
    book = ConfirmationBook(ttl_s=5, clock=clk)
    tok = book.request("arm", {}, "ARM")
    assert book.pending()[0]["token"] == tok
    clk.t += 6
    assert not book.consume(tok, "arm", {})
    assert book.pending() == []


def test_which_actions_need_confirmation():
    g = FlightGate(cfg())
    for a in ("arm", "takeoff", "set_param", "upload_mission", "start_mission"):
        assert g.needs_confirmation(a)
    for a in ("goto", "set_speed", "disarm"):
        assert not g.needs_confirmation(a)


# ------------------------------------------------------------ envelope
def test_modes():
    g = FlightGate(cfg())
    assert g.check_mode("guided").allowed
    for bad in ("ACRO", "STABILIZE", "FLIP", "SPORT"):
        assert not g.check_mode(bad).allowed
    assert g.is_safe_mode("rtl") and g.is_safe_mode("LAND") and not g.is_safe_mode("GUIDED")


def test_arm_requirements():
    g = FlightGate(cfg())
    assert g.check_arm(good_status(), GOOD_TELEM).allowed
    bad = g.check_arm(good_status(fix_type=1, battery=10, rc_link_ok=None, mode="AUTO"),
                      {"ekf": {"position_ok": False, "flag_names": ["attitude"]}})
    text = " ".join(bad.reasons)
    for frag in ("GPS fix", "battery", "RC/ELRS", "EKF", "mode AUTO"):
        assert frag in text
    assert not g.check_arm(good_status(armed=True), GOOD_TELEM).allowed


def test_arm_battery_unknown_is_not_a_block():
    g = FlightGate(cfg())
    assert g.check_arm(good_status(battery=-1), GOOD_TELEM).allowed


def test_arm_rc_not_required_in_sitl_profile():
    g = FlightGate(cfg(require_rc_link=False))
    assert g.check_arm(good_status(rc_link_ok=None), GOOD_TELEM).allowed


def test_disarm_refused_in_the_air():
    g = FlightGate(cfg())
    assert not g.check_disarm(good_status(armed=True, rel_alt=8.0)).allowed
    assert g.check_disarm(good_status(armed=True, rel_alt=0.1)).allowed


def test_takeoff_limits():
    g = FlightGate(cfg())
    ok = good_status(armed=True)
    assert g.check_takeoff(10, ok).allowed
    assert not g.check_takeoff(31, ok).allowed
    assert not g.check_takeoff(1, ok).allowed
    assert not g.check_takeoff(10, good_status(armed=True, mode="LOITER")).allowed
    assert not g.check_takeoff(10, good_status()).allowed


def test_goto_geofence_and_altitude():
    g = FlightGate(cfg())
    st = good_status(armed=True, rel_alt=10)
    near = (HOME["lat"] + 0.0005, HOME["lon"])          # ~55 m north
    far = (HOME["lat"] + 0.005, HOME["lon"])            # ~555 m north
    assert g.check_goto(*near, 10, st, HOME).allowed
    v = g.check_goto(*far, 10, st, HOME)
    assert not v.allowed and "geofence" in v.reasons[0]
    assert not g.check_goto(*near, 45, st, HOME).allowed
    assert not g.check_goto(*near, 10, st, None).allowed       # home unknown
    assert not g.check_goto(95.0, 0, 10, st, HOME).allowed     # nonsense lat


def test_speed():
    g = FlightGate(cfg())
    assert g.check_speed(3).allowed
    assert not g.check_speed(0) .allowed
    assert not g.check_speed(20).allowed


@pytest.mark.parametrize("name", ["FS_GCS_ENABLE", "FS_THR_ENABLE", "BATT_FS_LOW_ACT",
                                  "FENCE_ENABLE", "ARMING_CHECK", "SYSID_MYGCS",
                                  "SERIAL1_PROTOCOL", "MOT_SPIN_ARM"])
def test_failsafe_and_hardware_params_refused(name):
    assert not FlightGate(cfg()).check_param(name, 0).allowed


def test_allowlisted_param_range_both_firmware_spellings():
    g = FlightGate(cfg())
    assert g.check_param("WP_SPD", 4).allowed          # ArduCopter 4.7+
    assert not g.check_param("WP_SPD", 20).allowed
    assert g.check_param("wpnav_speed", 300).allowed   # <= 4.6, cm/s
    assert not g.check_param("WPNAV_SPEED", 5000).allowed


def test_mission_checks():
    g = FlightGate(cfg(max_mission_items=3))
    wps = [{"lat": HOME["lat"] + 0.0003, "lon": HOME["lon"], "alt_m": 12}]
    assert g.check_mission(wps, HOME).allowed
    assert not g.check_mission([], HOME).allowed
    assert not g.check_mission(wps * 4, HOME).allowed
    far = wps + [{"lat": HOME["lat"] + 0.01, "lon": HOME["lon"], "alt_m": 12}]
    v = g.check_mission(far, HOME)
    assert not v.allowed and "wp2" in v.reasons[0]
    v = g.check_mission(wps * 2, None)
    assert v.reasons.count("home position unknown - cannot verify geofence") == 1


def test_start_mission():
    g = FlightGate(cfg())
    assert g.check_start_mission(good_status(armed=True, rel_alt=10), 3).allowed
    assert not g.check_start_mission(good_status(armed=True, rel_alt=0), 3).allowed
    assert not g.check_start_mission(good_status(armed=True, rel_alt=10), 0).allowed


def test_describe_has_no_secrets():
    d = FlightGate(cfg(confirm_mode="operator")).describe()
    assert "max_alt_m" in d and "token" not in str(d).lower().replace("confirm", "")


# ------------------------------------------------------------ legacy gate kept
def test_legacy_spray_gate_still_importable():
    """The original SafetyGate (spray rules) is kept for tools.py."""
    assert hasattr(SafetyGate, "can_spray") and hasattr(SafetyGate, "can_arm")
