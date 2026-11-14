from galloc.exceptions import GallocError


class UnsupportedProvider(GallocError):
    """The selected provider has no adapter in this installation."""


class CreationRejected(GallocError):
    """The provider confirmed that creation did not produce any resources."""


class GPUUnavailable(CreationRejected):
    """The provider currently has no capacity for the selected GPU placement."""


class InvalidGPU(CreationRejected):
    """Indicate that a GPU selection cannot resolve to one supported type."""


class InvalidRegion(CreationRejected):
    """Indicate that a region does not identify a supported placement."""
