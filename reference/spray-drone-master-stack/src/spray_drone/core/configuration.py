"""Minimal validated configuration; deliberately contains no actuator controls."""

from dataclasses import dataclass

from spray_drone.core.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Non-actuating application configuration."""

    environment: str = "simulation"
    actuation_enabled: bool = False

    def validate(self) -> None:
        """Reject unsafe or unsupported initial configuration."""
        if self.environment not in {"simulation", "read_only"}:
            raise ConfigurationError(
                "Initial scaffold supports only simulation or read_only environments"
            )
        if self.actuation_enabled:
            raise ConfigurationError(
                "Actuation is not implemented or permitted by the initial scaffold"
            )
