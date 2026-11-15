import stat

from galloc.config import save_config
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
