class FeedDecodeError(Exception):
    """Raised when an upstream feed payload cannot be decoded into domain models."""


class UnknownLineError(Exception):
    """Raised when a line has no live vehicles (unknown or currently inactive)."""


class NotEnoughVehiclesError(Exception):
    """Raised when a line has too few live vehicles to re-space headways."""
