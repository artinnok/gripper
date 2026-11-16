import json

import pytest
from click.testing import CliRunner

from galloc.main import cli
from galloc.settings import Settings, load_settings


@pytest.mark.parametrize("source", ["environment", "dotenv"])
@pytest.mark.parametrize("configured", [False, True])
def test_legacy_sources_do_not_configure_application(tmp_path, monkeypatch, config_path, source, configured):
    """Ignore legacy credentials and invalid overrides, with or without a TOML file."""

    monkeypatch.chdir(tmp_path)
    legacy_values = {
        "GALLOC_PROVIDER": "unknown",
        "GALLOC_RUNPOD_API_KEY": "legacy-token",
        "GALLOC_API_TIMEOUT": "invalid-timeout",
        "GALLOC_DISK_GB": "0",
        "GALLOC_RUNPOD_DISK_GB": "0",
        "GALLOC_DEBUG": "true",
    }
    if source == "environment":
        for name, value in legacy_values.items():
            monkeypatch.setenv(name, value)
    else:
        (tmp_path / ".env").write_text("\n".join(f"{name}={value}" for name, value in legacy_values.items()))
    if configured:
        config_path.write_text('[defaults]\ndisk_gb = 100\n[providers.runpod]\napi_key = "toml-token"\ndisk_gb = 80\n')

    settings = load_settings(config_path)
    assert settings.provider == "runpod"
    assert settings.api_timeout == 30
    assert settings.debug is False
    if configured:
        assert settings.disk_gb == 100
        assert settings.runpod_disk_gb == 80
        assert settings.runpod_api_key.get_secret_value() == "toml-token"
    else:
        assert settings == Settings()

    result = CliRunner().invoke(cli, ["providers", "--output", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"name": "runpod", "configured": configured}]
    assert "legacy-token" not in result.output
    assert "toml-token" not in result.output


def test_toml_runtime_options_and_secret_output(write_config):
    """Load timeouts, debug and provider options from TOML while masking the API key."""

    config_path = write_config(
        defaults={"api_timeout": 12, "startup_timeout": 300, "startup_poll_interval": 5, "debug": True},
        runpod={"api_key": "private-token", "api_url": "https://custom.test/v2/", "image": "example/image:1"},
    )

    settings = load_settings(config_path)

    assert settings.api_timeout == 12
    assert settings.startup_timeout == 300
    assert settings.startup_poll_interval == 5
    assert settings.debug is True
    assert settings.database_path == config_path.parent / "state.db"
    assert settings.runpod_api_url == "https://custom.test/v2/"
    assert settings.runpod_image == "example/image:1"
    assert settings.runpod_api_key.get_secret_value() == "private-token"
    assert "private-token" not in repr(settings)
    assert "private-token" not in settings.model_dump_json()


def test_settings_and_help_work_without_provider_credentials(config_path):
    """Leave unselected providers unconfigured and keep help accessible."""

    assert load_settings(config_path).runpod_api_key is None
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Usage:" in result.output
