from pathlib import Path

from pydantic import BaseModel, PositiveFloat, PositiveInt, SecretStr

from galloc.config import read_config


class Settings(BaseModel):
    """Validate shared application settings with explicit values and built-in defaults."""

    ##########
    # COMMON #
    ##########
    provider: str = "runpod"
    disk_gb: PositiveInt = 50
    ssh_public_key: Path | None = None
    database_path: Path = Path.home() / ".galloc" / "state.db"
    api_timeout: PositiveFloat = 30
    startup_timeout: PositiveFloat = 900
    startup_poll_interval: PositiveFloat = 2
    debug: bool = False

    #############
    # RUNPOD.AI #
    #############
    runpod_api_url: str = "https://api.runpod.io/v2/"
    runpod_api_key: SecretStr | None = None
    runpod_image: str = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
    runpod_disk_gb: PositiveInt | None = None
    runpod_ssh_public_key: Path | None = None

    ###########
    # VAST.AI #
    ###########
    vast_api_url: str = "https://console.vast.ai/api/v0/"
    vast_api_key: SecretStr | None = None
    vast_image: str = "vastai/base-image:@vastai-automatic-tag"
    vast_disk_gb: PositiveInt | None = None
    vast_ssh_public_key: Path | None = None


def load_settings(config_path: Path) -> Settings:
    """Read and validate the TOML configuration shared by CLI and TUI."""

    return Settings(**read_config(config_path))
