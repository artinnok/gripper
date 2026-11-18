import stat

import pytest
from pydantic import ValidationError

from galloc.config import save_config, save_config_value
from galloc.exceptions import InvalidConfig
from galloc.providers.factory import get_ssh_public_key_path
from galloc.settings import load_settings


@pytest.mark.parametrize("provider_name", ["runpod", "vast"])
def test_saved_configuration_supplies_provider_settings(tmp_path, config_path, provider_name):
    """Read the saved provider and key back through the shared settings model."""

    save_config(
        config_path,
        provider_name=provider_name,
        api_key='key-with-"quote"',
        disk_gb=100,
        ssh_public_key_path=tmp_path / "key.pub",
    )
    settings = load_settings(config_path)

    assert settings.provider == provider_name
    assert settings.disk_gb == getattr(settings, f"{provider_name}_disk_gb") == 100
    assert getattr(settings, f"{provider_name}_api_key").get_secret_value() == 'key-with-"quote"'
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("provider_name", ["runpod", "vast"])
@pytest.mark.parametrize(
    "provider_values, expected_disk, expected_key",
    [
        ("", 100, "shared.pub"),
        ('disk_gb = 80\nssh_public_key = "~/.ssh/provider.pub"', 80, "provider.pub"),
    ],
)
def test_shared_defaults_and_provider_overrides(
    config_path, tmp_path, monkeypatch, provider_values, expected_disk, expected_key, provider_name
):
    """Resolve shared values and provider overrides through the same settings used by both interfaces."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path.write_text(
        f'[defaults]\nprovider = "{provider_name}"\ndisk_gb = 100\nssh_public_key = "~/.ssh/shared.pub"\n'
        f'[providers.{provider_name}]\napi_key = "file-token"\n{provider_values}\n'
    )

    settings = load_settings(config_path)

    assert getattr(settings, f"{provider_name}_disk_gb") == expected_disk
    assert get_ssh_public_key_path(settings, provider_name) == tmp_path / ".ssh" / expected_key


@pytest.mark.parametrize("section", ["defaults", "providers.runpod", "providers.vast"])
def test_invalid_disk_size_is_rejected(config_path, section):
    """Validate shared and provider disk sizes before opening a provider."""

    config_path.write_text(f"[{section}]\ndisk_gb = 0\n")
    with pytest.raises(ValidationError):
        load_settings(config_path)


@pytest.mark.parametrize(
    "value",
    [
        '[defaults]\napi_key = "private-token"',
        '[defaults]\nregion = "EU-NL-1"',
        '[providers.runpod]\nprovider = "runpod"',
        '[providers.runpod]\nregion = "EU-NL-1"',
    ],
)
def test_misplaced_configuration_values_are_explained(config_path, value):
    """Keep provider selection, credentials, and per-creation region in their agreed locations."""

    config_path.write_text(value)
    with pytest.raises(InvalidConfig) as failure:
        load_settings(config_path)
    assert "private-token" not in str(failure.value)


def test_saving_existing_configuration_restricts_file_permissions(config_path, tmp_path):
    """Keep credentials private even when replacing a file created by a text editor."""

    config_path.touch(mode=0o644)
    config_path.chmod(0o644)

    save_config(
        config_path, provider_name="runpod", api_key="file-token", disk_gb=50, ssh_public_key_path=tmp_path / "key.pub"
    )

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        ("", '[providers.vast]\napi_key = "new-key"\n'),
        (
            '# notes\n[defaults]\nprovider = "runpod"  # default\n',
            '# notes\n[defaults]\nprovider = "runpod"  # default\n\n[providers.vast]\napi_key = "new-key"\n',
        ),
        (
            '[providers.vast]\ndisk_gb = 80\n\n[providers.runpod]\napi_key = "other"\n',
            '[providers.vast]\napi_key = "new-key"\ndisk_gb = 80\n\n[providers.runpod]\napi_key = "other"\n',
        ),
        (
            '[providers.runpod]\napi_key = "other"\n[providers.vast]\n# rotated\napi_key = "old-key"\n',
            '[providers.runpod]\napi_key = "other"\n[providers.vast]\n# rotated\napi_key = "new-key"\n',
        ),
    ],
)
def test_one_value_is_saved_without_rewriting_the_file(config_path, saved, expected):
    """Append a table, add a line to a table, or replace a line, keeping comments and other values."""

    config_path.write_text(saved)
    config_path.chmod(0o644)

    save_config_value(config_path, "providers.vast", "api_key", "new-key")

    assert config_path.read_text() == expected
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert load_settings(config_path).vast_api_key.get_secret_value() == "new-key"


@pytest.mark.parametrize(
    "saved", ["providers = { vast = { disk_gb = 80 } }\n", '[providers."vast"]\ndisk_gb = 80\n', "["]
)
def test_unusual_layout_is_left_for_manual_editing(config_path, saved):
    """Refuse a file this writer cannot change safely instead of damaging it."""

    config_path.write_text(saved)

    with pytest.raises(InvalidConfig) as failure:
        save_config_value(config_path, "providers.vast", "api_key", "private-token")

    assert "manually" in str(failure.value)
    assert "private-token" not in str(failure.value)
    assert config_path.read_text() == saved
