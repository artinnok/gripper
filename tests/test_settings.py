from click.testing import CliRunner

from galloc.main import cli
from galloc.settings import Settings


def test_runpod_configuration_priority_and_secret_output(tmp_path, monkeypatch):
    """Read provider settings centrally and keep the token out of representations."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GALLOC_RUNPOD_IMAGE", raising=False)
    monkeypatch.delenv("GALLOC_RUNPOD_DISK_GB", raising=False)
    (tmp_path / ".env").write_text(
        "GALLOC_RUNPOD_API_KEY=file-token\n"
        "GALLOC_RUNPOD_API_URL=https://file.example/v2/\n"
        "GALLOC_RUNPOD_IMAGE=example/image:1\n"
        "GALLOC_RUNPOD_DISK_GB=80\n"
    )
    monkeypatch.setenv("GALLOC_RUNPOD_API_KEY", "environment-token")
    monkeypatch.setenv("GALLOC_RUNPOD_API_URL", "https://environment.example/v2/")

    settings = Settings()

    assert settings.runpod_api_key.get_secret_value() == "environment-token"
    assert settings.runpod_api_url == "https://environment.example/v2/"
    assert settings.runpod_image == "example/image:1"
    assert settings.runpod_disk_gb == 80
    assert "environment-token" not in repr(settings)
    assert "environment-token" not in settings.model_dump_json()

    monkeypatch.delenv("GALLOC_RUNPOD_API_KEY")
    monkeypatch.delenv("GALLOC_RUNPOD_API_URL")
    assert Settings().runpod_api_key.get_secret_value() == "file-token"
    assert Settings().runpod_api_url == "https://file.example/v2/"


def test_settings_and_help_work_without_provider_credentials(tmp_path, monkeypatch):
    """Leave unselected providers unconfigured and keep help accessible."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GALLOC_RUNPOD_API_KEY", raising=False)

    assert Settings().runpod_api_key is None
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Usage:" in result.output
