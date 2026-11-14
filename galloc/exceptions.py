class GallocError(Exception):
    """An expected application failure with a message suitable for the user."""


class InvalidSSHKey(GallocError):
    """The supplied value is not a valid OpenSSH public key."""
