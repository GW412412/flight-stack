"""Stage 0 invariants: every flight-hardware TOML in config/ must ship safe.

These run in CI, so a profile edit that opens the safety envelope (actuation on,
dry-run off, echo confirmations, wide fence, AUTO/manual modes for the model...)
fails the build and has to be argued in review."""
from pathlib import Path

import pytest

from stack_config import RELAXED_PROFILES, load_config

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
STACK_TOMLS = sorted(CONFIG_DIR.glob("llm_stack.*.toml"))
HARDWARE = [p for p in STACK_TOMLS if load_config(str(p)).profile not in RELAXED_PROFILES]
FORBIDDEN_MODES = {"AUTO", "STABILIZE", "ACRO", "SPORT", "DRIFT", "FLIP", "AUTOTUNE",
                   "THROW", "ACRO_HELI", "SYSTEMID", "AUTO_RTL", "TURTLE"}


def test_a_hardware_profile_exists():
    assert HARDWARE, "no hardware profile found in config/ (invariants would pass vacuously)"


def ids(paths):
    return [p.name for p in paths]


@pytest.fixture(params=HARDWARE, ids=ids(HARDWARE))
def hw(request, monkeypatch):
    for k in ("DRONE_ACTUATION", "DRONE_DRY_RUN", "DRONE_CONFIRM_MODE", "MCP_HOST",
              "DRONE_STACK_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    return load_config(str(request.param))


def test_ships_read_only_and_dry_run(hw):
    assert hw.safety.actuation_enabled is False
    assert hw.safety.dry_run is True


def test_operator_confirmation_and_pilot_in_charge(hw):
    s = hw.safety
    assert s.confirm_mode == "operator"
    assert s.require_rc_link is True
    assert s.pilot_override_detection is True


def test_envelope_is_small(hw):
    s = hw.safety
    assert 0 < s.max_alt_m <= 30
    assert 0 < s.geofence_radius_m <= 100
    assert 0 < s.max_speed_ms <= 5
    assert s.min_goto_alt_m >= 2
    assert 0 < s.max_actions_per_minute <= 30 and s.min_action_interval_s >= 1


def test_needs_gps_and_battery_margin(hw):
    assert hw.safety.require_gps_fix >= 3
    assert hw.safety.min_battery_pct >= 30


def test_model_cannot_use_manual_modes_or_bare_auto(hw):
    modes = set(hw.safety.allowed_modes)
    assert not modes & FORBIDDEN_MODES, modes & FORBIDDEN_MODES
    assert {"LAND", "RTL"} <= modes                 # the calming actions stay available


def test_param_allowlist_ranges_are_sane(hw):
    assert hw.safety.param_allowlist
    for name, (lo, hi) in hw.safety.param_allowlist.items():
        assert lo < hi, name
        assert lo >= 0, name


def test_network_surface_is_loopback(hw):
    assert hw.server.host == "127.0.0.1"
    assert hw.mavlink.url.startswith(("udpin:127.0.0.1", "tcp:127.0.0.1", "udp:127.0.0.1"))


def test_sitl_profile_is_loopback_too():
    for p in set(STACK_TOMLS) - set(HARDWARE):
        assert load_config(str(p)).server.host == "127.0.0.1", p.name
