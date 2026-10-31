"""P1 #7: environment overrides may only TIGHTEN a hardware profile."""
from pathlib import Path

import pytest

from stack_config import load_config

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
HW = str(CONFIG_DIR / "llm_stack.pixhawk.toml")      # file name kept; profile = hardware
SITL = str(CONFIG_DIR / "llm_stack.sitl.toml")
ENVS = ("DRONE_ACTUATION", "DRONE_DRY_RUN", "DRONE_CONFIRM_MODE", "MCP_HOST", "MCP_PORT",
        "MAVLINK_URL", "DRONE_STACK_CONFIG")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENVS:
        monkeypatch.delenv(k, raising=False)


def hw(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return load_config(HW)


def test_hardware_file_is_the_safe_baseline():
    c = load_config(HW)
    assert (c.safety.actuation_enabled, c.safety.dry_run, c.safety.confirm_mode) == \
        (False, True, "operator")
    assert c.ignored_env == []


@pytest.mark.parametrize("env", [
    {"DRONE_ACTUATION": "1"}, {"DRONE_ACTUATION": "true"}, {"DRONE_ACTUATION": "on"},
    {"DRONE_DRY_RUN": "0"}, {"DRONE_DRY_RUN": "false"},
    {"DRONE_CONFIRM_MODE": "echo"},
    {"DRONE_ACTUATION": "1", "DRONE_DRY_RUN": "0", "DRONE_CONFIRM_MODE": "echo"}])
def test_env_cannot_loosen_the_hardware_profile(monkeypatch, env):
    c = hw(monkeypatch, **env)
    assert c.safety.actuation_enabled is False
    assert c.safety.dry_run is True
    assert c.safety.confirm_mode == "operator"
    assert len(c.ignored_env) == len(env)


def test_env_can_still_tighten(monkeypatch, tmp_path):
    loose = tmp_path / "hw.toml"          # a hardware-profile file that was opened up on purpose
    loose.write_text('profile = "bench"\n[safety]\nactuation_enabled = true\n'
                     'dry_run = false\nconfirm_mode = "echo"\n')
    monkeypatch.setenv("DRONE_ACTUATION", "0")
    monkeypatch.setenv("DRONE_DRY_RUN", "1")
    monkeypatch.setenv("DRONE_CONFIRM_MODE", "operator")
    c = load_config(str(loose))
    assert (c.safety.actuation_enabled, c.safety.dry_run, c.safety.confirm_mode) == \
        (False, True, "operator")
    assert c.ignored_env == []


def test_same_value_as_the_file_is_not_a_loosening(monkeypatch, tmp_path):
    f = tmp_path / "hw.toml"
    f.write_text('profile = "bench"\n[safety]\nactuation_enabled = true\ndry_run = false\n')
    monkeypatch.setenv("DRONE_ACTUATION", "1")
    monkeypatch.setenv("DRONE_DRY_RUN", "0")
    c = load_config(str(f))
    assert c.safety.actuation_enabled and not c.safety.dry_run and c.ignored_env == []


def test_hardware_detection_is_by_exclusion(monkeypatch, tmp_path):
    """Any profile name other than sitl/default is flight hardware, including
    a missing or odd one, so a rename cannot switch the protection off."""
    for name in ("pixhawk", "omnibus", "radio", "", "SITL-but-not", "prod"):
        f = tmp_path / "p.toml"
        f.write_text(f'profile = "{name}"\n')
        monkeypatch.setenv("DRONE_ACTUATION", "1")
        assert load_config(str(f)).safety.actuation_enabled is False, name


def test_sitl_and_default_keep_their_env_overrides(monkeypatch):
    monkeypatch.setenv("DRONE_CONFIRM_MODE", "operator")
    monkeypatch.setenv("DRONE_DRY_RUN", "1")
    c = load_config(SITL)
    assert c.safety.confirm_mode == "operator" and c.safety.dry_run is True
    monkeypatch.setenv("DRONE_ACTUATION", "1")
    assert load_config().safety.actuation_enabled is True            # no file = default
    assert load_config().ignored_env == []
    monkeypatch.delenv("DRONE_CONFIRM_MODE")
    assert load_config(SITL).safety.confirm_mode == "echo"


def test_invalid_confirm_mode_env_is_an_error(monkeypatch):
    monkeypatch.setenv("DRONE_CONFIRM_MODE", "yolo")
    with pytest.raises(ValueError):
        load_config(HW)
