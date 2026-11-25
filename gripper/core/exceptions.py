from gripper.exceptions import GripperError


class InvalidTTL(GripperError):
    """The requested TTL is invalid or exceeds the supported date range."""


class InvalidBudget(GripperError):
    """The compute budget is invalid or cannot produce a usable TTL."""


class GPUNotFound(GripperError):
    """The requested GPU has no local mapping or no existing provider instance."""


class GPUConflict(GripperError):
    """The requested operation conflicts with an identity mapping or current provider state."""


class GPUCreationPending(GripperError):
    """Creation has no confirmed result and must be reconciled before completion."""


class InstanceNotReady(GripperError):
    """The instance failed to become ready within the startup wait."""
