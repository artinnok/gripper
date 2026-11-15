class GallocError(Exception):
    """An expected application failure with a message suitable for the user."""


class InvalidConfig(GallocError):
    """The user configuration file cannot be read as TOML."""


class InvalidSSHKey(GallocError):
    """The supplied value is not a valid OpenSSH public key."""


class SSHKeyGenerationFailed(GallocError):
    """OpenSSH did not create the requested key pair."""
