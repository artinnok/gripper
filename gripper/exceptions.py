class GripperError(Exception):
    """An expected application failure with a message suitable for the user."""


class InvalidConfig(GripperError):
    """The user configuration file cannot be read as TOML."""


class InvalidSSHKey(GripperError):
    """The supplied value is not a valid OpenSSH public key."""


class SSHKeyGenerationFailed(GripperError):
    """OpenSSH did not create the requested key pair."""
