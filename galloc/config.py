import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic_settings import TomlConfigSettingsSource

from galloc.exceptions import InvalidConfig


CONFIG_PATH = Path.home() / ".galloc" / "config.toml"


class ConfigFileSource(TomlConfigSettingsSource):
    """Read the user configuration file and expose its sections as flat setting names."""

    def _read_file(self, file_path: Path) -> dict[str, Any]:
        """Keep [defaults] names and prefix [providers.<name>] values with the provider name."""

        try:
            config = super()._read_file(file_path)
        except tomllib.TOMLDecodeError, UnicodeError:
            raise InvalidConfig(f"Invalid TOML in {file_path}.") from None

        value_map = dict(config.get("defaults", {}))
        for provider_name, provider_config in config.get("providers", {}).items():
            for name, value in provider_config.items():
                value_map[f"{provider_name}_{name}"] = value

        return value_map


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
