from pathlib import Path

from gripper.config import save_config_value
from gripper.core.ssh_keys import configured_key_path, read_local_key_pair
from gripper.exceptions import InvalidSSHKey
from gripper.providers.factory import open_provider
from gripper.settings import Settings


def find_registered_key(settings: Settings, provider_name: str) -> Path | None:
    """Find a local key pair whose fingerprint is registered with the selected provider."""

    with open_provider(settings, provider_name) as provider:
        fingerprint_set = {key.fingerprint for key in provider.get_ssh_key_list()}

    path_list = sorted((Path.home() / ".ssh").glob("*.pub"))
    preferred_path = configured_key_path(settings, provider_name)
    if preferred_path is not None:
        path_list.insert(0, preferred_path)
    for path in path_list:
        try:
            key = read_local_key_pair(path)
        except InvalidSSHKey, OSError:
            continue
        if key.fingerprint in fingerprint_set:
            return path
    return None


def remember_key(settings: Settings, config_path: Path, provider_name: str, public_key_path: Path) -> Settings:
    """Remember only the local key path so later additions and SSH commands use the same identity."""

    path = public_key_path.expanduser().absolute()
    if configured_key_path(settings, provider_name) == path:
        return settings

    save_config_value(config_path, f"providers.{provider_name}", "ssh_public_key", str(path))
    return settings.model_copy(update={f"{provider_name}_ssh_public_key": path})
