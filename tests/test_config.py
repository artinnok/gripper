import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from galloc.config import save_config
from galloc.exceptions import InvalidConfig
from galloc.providers.factory import get_ssh_public_key_path
from galloc.settings import Settings


def test_saved_configuration_supplies_provider_settings(tmp_path, monkeypatch, config_path):
    """Read the saved provider and key back through the shared settings model."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GALLOC_PROVIDER", raising=False)
    monkeypatch.delenv("GALLOC_RUNPOD_API_KEY", raising=False)

    save_config(
        config_path, provider_name="runpod", api_key='key-with-"quote"', ssh_public_key_path=tmp_path / "key.pub"
    )
    settings = Settings()

    assert settings.provider == "runpod"
    assert settings.runpod_api_key.get_secret_value() == 'key-with-"quote"'
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "provider_values, expected_disk, expected_key",
    [
        ("", 100, "shared.pub"),
        ('disk_gb = 80\nssh_public_key = "~/.ssh/provider.pub"', 80, "provider.pub"),
    ],
)
def test_shared_defaults_and_provider_overrides(
    config_path, tmp_path, monkeypatch, provider_values, expected_disk, expected_key
):
    """Resolve shared values and provider overrides through the same settings used by both interfaces."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path.write_text(
        '[defaults]\nprovider = "runpod"\ndisk_gb = 100\nssh_public_key = "~/.ssh/shared.pub"\n'
        f'[providers.runpod]\napi_key = "file-token"\n{provider_values}\n'
    )

    settings = Settings()

    assert settings.runpod_disk_gb == expected_disk
    assert get_ssh_public_key_path(settings, "runpod") == tmp_path / ".ssh" / expected_key


def test_environment_defaults_override_toml_provider_values(config_path, tmp_path, monkeypatch):
    """Prefer shared environment values to TOML and more specific environment values to shared ones."""

    monkeypatch.chdir(tmp_path)
    config_path.write_text('[defaults]\ndisk_gb = 100\n[providers.runpod]\ndisk_gb = 80\nssh_public_key = "file.pub"\n')
    monkeypatch.setenv("GALLOC_DISK_GB", "60")
    monkeypatch.setenv("GALLOC_SSH_PUBLIC_KEY", "shared-env.pub")
    assert Settings().runpod_disk_gb == 60
    assert get_ssh_public_key_path(Settings(), "runpod") == Path("shared-env.pub")

    monkeypatch.setenv("GALLOC_RUNPOD_DISK_GB", "40")
    monkeypatch.setenv("GALLOC_RUNPOD_SSH_PUBLIC_KEY", "provider-env.pub")
    assert Settings().runpod_disk_gb == 40
    assert get_ssh_public_key_path(Settings(), "runpod") == Path("provider-env.pub")

    assert Settings(runpod_disk_gb=20).runpod_disk_gb == 20


@pytest.mark.parametrize("section", ["defaults", "providers.runpod"])
def test_invalid_disk_size_is_rejected(config_path, section):
    """Validate shared and provider disk sizes before opening a provider."""

    config_path.write_text(f"[{section}]\ndisk_gb = 0\n")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


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
        Settings(_env_file=None)
    assert "private-token" not in str(failure.value)


def test_environment_overrides_saved_configuration(tmp_path, monkeypatch, config_path):
    """Keep environment variables above the user configuration file."""

    monkeypatch.chdir(tmp_path)
    save_config(config_path, provider_name="runpod", api_key="file-token", ssh_public_key_path=tmp_path / "key.pub")
    monkeypatch.setenv("GALLOC_RUNPOD_API_KEY", "environment-token")

    assert Settings().runpod_api_key.get_secret_value() == "environment-token"


def test_saving_existing_configuration_restricts_file_permissions(config_path, tmp_path):
    """Keep credentials private even when replacing a file created by a text editor."""

    config_path.touch(mode=0o644)
    config_path.chmod(0o644)

    save_config(config_path, provider_name="runpod", api_key="file-token", ssh_public_key_path=tmp_path / "key.pub")

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
