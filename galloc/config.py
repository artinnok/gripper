import json
import tomllib
from pathlib import Path
from typing import Any

from galloc.exceptions import InvalidConfig


CONFIG_PATH = Path.home() / ".galloc" / "config.toml"
SHARED_SETTING_NAME_LIST = ("disk_gb", "ssh_public_key")


def read_config(config_path: Path) -> dict[str, Any]:
    """Read TOML defaults and provider overrides as flat setting names."""

    try:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError, UnicodeError:
        raise InvalidConfig(f"Invalid TOML in {config_path}.") from None

    value_map = dict(config.get("defaults", {}))
    if "api_key" in value_map or "region" in value_map:
        raise InvalidConfig("Put api_key in [providers.<name>]; choose region when adding a GPU.")

    shared_defaults = {name: value_map[name] for name in SHARED_SETTING_NAME_LIST if name in value_map}
    for provider_name, provider_config in config.get("providers", {}).items():
        if "provider" in provider_config or "region" in provider_config:
            raise InvalidConfig("Put provider in [defaults]; choose region when adding a GPU.")
        for name, value in (shared_defaults | provider_config).items():
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
