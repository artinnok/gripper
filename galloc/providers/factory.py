from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from galloc.api_clients.runpod.client import RunpodAPIClient
from galloc.providers.base import Provider
from galloc.providers.exceptions import UnsupportedProvider
from galloc.providers.runpod.provider import RunpodProvider
from galloc.settings import Settings


PROVIDER_NAME_LIST = ("runpod",)


@contextmanager
def open_provider(settings: Settings, name: str) -> Iterator[Provider]:
    """Build the selected adapter from settings and close its HTTP client after use."""

    if name not in PROVIDER_NAME_LIST:
        raise UnsupportedProvider(f"Unsupported provider: {name}.")

    with RunpodAPIClient(
        settings.runpod_api_key, base_url=settings.runpod_api_url, timeout=settings.api_timeout
    ) as api_client:
        yield RunpodProvider(
            api_client, image=settings.runpod_image, disk_gb=settings.runpod_disk_gb or settings.disk_gb
        )


def get_ssh_public_key_path(settings: Settings, provider_name: str) -> Path | None:
    """Resolve the selected provider's public key, falling back to the shared default."""

    return getattr(settings, f"{provider_name}_ssh_public_key", None) or settings.ssh_public_key
