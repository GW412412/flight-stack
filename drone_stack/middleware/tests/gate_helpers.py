"""Shared fixtures for FlightGate unit tests (plain dicts, no MAVLink)."""

HOME = {"lat": -35.363261, "lon": 149.165230, "alt_msl_m": 584.0}

FRESH_AGES = {"position": 0.1, "attitude": 0.1, "sys_status": 0.1, "gps": 0.1, "ekf": 0.1,
              "landed": None}


def good_status(**kw):
    s = {"link_ok": True, "heartbeat_age_s": 0.5, "mode": "GUIDED", "armed": False,
         "fix_type": 3, "battery": 90, "rc_link_ok": True, "rel_alt": 0.0,
         "lat": HOME["lat"], "lon": HOME["lon"], "landed_state": None,
         "msg_age_s": dict(FRESH_AGES)}
    s.update(kw)
    return s


GOOD_TELEM = {"ekf": {"position_ok": True, "flag_names": []}, "rc": {"channels": [1500] * 8}}
