from galloc.exceptions import GallocError


class InvalidTTL(GallocError):
    """The requested TTL is invalid or exceeds the supported date range."""


class InvalidBudget(GallocError):
    """The compute budget is invalid or cannot produce a usable TTL."""


class GPUNotFound(GallocError):
    """The requested GPU has no local mapping or no existing provider instance."""


class GPUConflict(GallocError):
    """The requested operation conflicts with an identity mapping or current provider state."""


class GPUCreationPending(GallocError):
    """Creation has no confirmed result and must be reconciled before completion."""


class InstanceNotReady(GallocError):
    """The instance failed to become ready within the startup wait."""
