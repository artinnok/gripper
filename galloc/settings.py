from pathlib import Path

from pydantic import PositiveFloat, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from galloc.config import CONFIG_PATH, ConfigFileSource


class Settings(BaseSettings):
    """Load shared settings from the environment, a local .env file, and the user configuration file."""

    model_config = SettingsConfigDict(
        env_prefix="GALLOC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ##########
    # COMMON #
    ##########
    provider: str = "runpod"
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
    runpod_disk_gb: PositiveInt = 50

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Read the user configuration file after the environment and .env."""

        config_settings = ConfigFileSource(settings_cls, toml_file=CONFIG_PATH)
        return init_settings, env_settings, dotenv_settings, file_secret_settings, config_settings
