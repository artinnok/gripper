import stat

import pytest
from pydantic import ValidationError

from galloc.config import save_config
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
