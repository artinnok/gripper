from pathlib import Path

from galloc.settings import Settings


def configured_key_path(settings: Settings, provider_name: str) -> Path | None:
    """Resolve the provider's preferred public key, falling back to the shared default."""

    path = getattr(settings, f"{provider_name}_ssh_public_key") or settings.ssh_public_key
    return path.expanduser().absolute() if path is not None else None
