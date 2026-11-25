import json
import re
import tomllib
from pathlib import Path
from typing import Any

from gripper.exceptions import InvalidConfig


SHARED_SETTING_NAME_LIST = ("disk_gb", "ssh_public_key")


def data_directory(home: Path) -> Path:
    """Keep existing GPU deadlines and credentials together when upgrading from galloc."""

    current = home / ".gripper"
    legacy = home / ".galloc"
    for directory in (current, legacy):
        if (directory / "config.toml").is_file() or (directory / "state.db").is_file():
            return directory
    return current


CONFIG_PATH = data_directory(Path.home()) / "config.toml"


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


def save_config(config_path: Path, *, provider_name: str, api_key: str, disk_gb: int) -> None:
    """Write the complete user configuration with owner-only access."""

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.touch(mode=0o600)
    config_path.chmod(0o600)
    config_path.write_text(
        "[defaults]\n"
        f"provider = {json.dumps(provider_name)}\n"
        f"disk_gb = {disk_gb}\n"
        "\n"
        f"[providers.{provider_name}]\n"
        f"api_key = {json.dumps(api_key)}\n",
        encoding="utf-8",
    )


def save_config_value(config_path: Path, table: str, name: str, value: str) -> None:
    """Set one string in one table, keeping every other line of the file as the user wrote it."""

    text = config_path.read_text(encoding="utf-8")
    updated_text = _set_line(text, table, name, f"{name} = {json.dumps(value)}")

    failure = InvalidConfig(f"Could not update {config_path}. Set {name} in [{table}] manually.")
    try:
        expected_config = tomllib.loads(text)
        section = expected_config
        for key in table.split("."):
            section = section.setdefault(key, {})
        section[name] = value
        updated_config = tomllib.loads(updated_text)

    except tomllib.TOMLDecodeError:
        raise failure from None

    if updated_config != expected_config:
        raise failure

    config_path.chmod(0o600)
    config_path.write_text(updated_text, encoding="utf-8")


def _set_line(text: str, table: str, name: str, assignment: str) -> str:
    """Replace the name's line in its table, add the line to the table, or append the table."""

    header = f"[{table}]"
    line_list = text.splitlines()
    stripped_list = [line.strip() for line in line_list]
    if header not in stripped_list:
        return "\n".join([*line_list, "", header, assignment, ""]).lstrip("\n")

    first = stripped_list.index(header) + 1
    for index in range(first, len(line_list)):
        if stripped_list[index].startswith("["):
            break
        if re.match(rf"{re.escape(name)}\s*=", stripped_list[index]):
            line_list[index] = assignment
            return "\n".join([*line_list, ""])

    line_list.insert(first, assignment)
    return "\n".join([*line_list, ""])
