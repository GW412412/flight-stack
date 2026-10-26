"""Tests for the safe, non-actuating initial configuration."""

import pytest

from spray_drone.core.configuration import AppConfig
from spray_drone.core.exceptions import ConfigurationError


def test_default_configuration_is_simulation_and_non_actuating() -> None:
    config = AppConfig()
    config.validate()
    assert config.environment == "simulation"
    assert config.actuation_enabled is False


def test_read_only_configuration_is_allowed() -> None:
    AppConfig(environment="read_only").validate()


def test_live_environment_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        AppConfig(environment="live").validate()


def test_actuation_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        AppConfig(actuation_enabled=True).validate()
