from pathlib import Path

from pydantic import PositiveFloat, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load shared settings from the environment and a local .env file."""

    model_config = SettingsConfigDict(
        env_prefix="GALLOC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ##########
    # COMMON #
    ##########
    database_path: Path = Path.home() / ".galloc" / "state.db"
    scheduler_interval: PositiveInt = 30
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
    runpod_disk_gb: PositiveInt = 50
