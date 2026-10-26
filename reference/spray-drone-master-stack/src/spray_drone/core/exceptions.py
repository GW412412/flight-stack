"""Domain exceptions used by the initial scaffold."""


class SprayDroneError(Exception):
    """Base exception for expected application-level errors."""


class ConfigurationError(SprayDroneError):
    """Raised when configuration is missing or invalid."""
