from textual.markup import escape
from textual.widgets import Link

from galloc.providers.factory import is_configured
from galloc.settings import Settings


API_KEY_NOTE_MAP = {
    "runpod": "• Pods read/write\n• Catalog read\n• Authentication read/write",
    "vast": "• instance_read\n• instance_write\n• user_read\n• user_write\n• misc",
    "lambda": "Workspace role: Workspace Member\nInstance types: read\nInstances: launch, read, terminate\nSSH keys: create, read, delete",
}

API_KEY_URL_MAP = {
    "runpod": "https://console.runpod.io/user/credentials",
    "vast": "https://cloud.vast.ai/manage-keys/",
    "lambda": "https://cloud.lambda.com/user/api-keys",
}


class ProviderKeyLink(Link, can_focus=False):
    """Open the provider's credentials page without taking focus from the key input."""

    def __init__(self) -> None:
        """Start hidden until a provider is selected."""

        super().__init__("")
        self.display = False

    def show_provider(self, provider_name: str) -> None:
        """Show the browser destination and the keyboard action alongside it."""

        self.url = API_KEY_URL_MAP[provider_name]
        self.text = f"F2 Open API keys\n{self.url.removeprefix('https://')}"
        self.display = True


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
