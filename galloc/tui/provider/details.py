from textual.markup import escape

from galloc.providers.factory import is_configured
from galloc.settings import Settings


API_KEY_NOTE_MAP = {
    "runpod": "• Pods read/write\n• Catalog read\n• Authentication read/write\n\nconsole.runpod.io/user/credentials",
    "vast": "• instance_read\n• instance_write\n• user_read\n• user_write\n• misc\n\ncloud.vast.ai/manage-keys/",
    "lambda": "Workspace role: Workspace Member\nInstance types: read\nInstances: launch, read, terminate\nSSH keys: create, read, delete",
}


def format_provider(settings: Settings, provider_name: str) -> str:
    """Show a selected provider's status and the API key permissions galloc needs from it."""

    return (
        f"[b $primary]{escape(provider_name)}[/]\n"
        f"{format_status(settings, provider_name)}\n\n"
        f"[$muted]api key permissions[/]\n{API_KEY_NOTE_MAP[provider_name]}"
    )


def format_status(settings: Settings, provider_name: str) -> str:
    """Name the configuration status the same way in the table and in the detail column."""

    if is_configured(settings, provider_name):
        return "configured"
    return "not configured"
