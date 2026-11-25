from gripper.exceptions import GripperError


class UnsupportedProvider(GripperError):
    """The selected provider has no adapter in this installation."""


class CreationRejected(GripperError):
    """The provider confirmed that creation did not produce any resources."""


class GPUUnavailable(CreationRejected):
    """The provider currently has no capacity for the selected GPU placement."""


class InvalidGPU(CreationRejected):
    """Indicate that a GPU selection cannot resolve to one supported type."""


class InvalidRegion(CreationRejected):
    """Indicate that a region does not identify a supported placement."""
