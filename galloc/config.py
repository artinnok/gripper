import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic_settings import TomlConfigSettingsSource

from galloc.exceptions import InvalidConfig


CONFIG_PATH = Path.home() / ".galloc" / "config.toml"
SHARED_SETTING_NAME_LIST = ("disk_gb", "ssh_public_key")


class ConfigFileSource(TomlConfigSettingsSource):
    """Read the user configuration file and expose its sections as flat setting names."""

    def __call__(self) -> dict[str, Any]:
        """Keep [defaults] names and prefix [providers.<name>] values with the provider name."""

        config = self.toml_data
        value_map = dict(config.get("defaults", {}))
        if "api_key" in value_map or "region" in value_map:
            raise InvalidConfig("Put api_key in [providers.<name>]; choose region when adding a GPU.")

        # Each provider inherits the file's shared defaults; a shared value from the environment overrides both.
        shared_defaults = {name: value_map[name] for name in SHARED_SETTING_NAME_LIST if name in value_map}
        shared_overrides = {
            name: self.current_state[name] for name in SHARED_SETTING_NAME_LIST if name in self.current_state
        }
        for provider_name, provider_config in config.get("providers", {}).items():
            if "provider" in provider_config or "region" in provider_config:
                raise InvalidConfig("Put provider in [defaults]; choose region when adding a GPU.")
            for name, value in (shared_defaults | provider_config | shared_overrides).items():
                value_map[f"{provider_name}_{name}"] = value

        return value_map

    def _read_file(self, file_path: Path) -> dict[str, Any]:
        """Report invalid TOML without including credential values in the error."""

        try:
            return super()._read_file(file_path)
        except tomllib.TOMLDecodeError, UnicodeError:
            raise InvalidConfig(f"Invalid TOML in {file_path}.") from None


def save_config(config_path: Path, *, provider_name: str, api_key: str, ssh_public_key_path: Path) -> None:
    """Write the complete user configuration with owner-only access."""

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.touch(mode=0o600)
    config_path.chmod(0o600)
    config_path.write_text(
        "[defaults]\n"
        f"provider = {json.dumps(provider_name)}\n"
        f"ssh_public_key = {json.dumps(str(ssh_public_key_path))}\n"
        "\n"
        f"[providers.{provider_name}]\n"
        f"api_key = {json.dumps(api_key)}\n",
        encoding="utf-8",
    )
