import asyncio

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Input, Static

from galloc.exceptions import GallocError
from galloc.providers.factory import PROVIDER_NAME_LIST, is_configured
from galloc.tui import operations
from galloc.tui.message import Message
from galloc.tui.provider.details import API_KEY_NOTE_MAP, format_provider, format_status


LIST_KEYS = "[$foreground]a[/] api key   [$foreground]u[/] use as default   [$foreground]esc[/] GPUs"
PROMPT_KEYS = "[$foreground]enter[/] save   [$foreground]esc[/] cancel"


class ProviderListScreen(Screen[None]):
    """List providers with their configuration status and take an API key in the detail column."""

    BINDINGS = [
        Binding("a", "api_key", "API key"),
        Binding("u", "use", "Use as default"),
        Binding("escape", "back", "Back"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self) -> None:
        """Start with no API key being entered or saved."""

        super().__init__()
        self.pending_provider: str | None = None
        self.busy = False

    def compose(self) -> ComposeResult:
        """Reuse the GPU screen's list and detail layout, with a masked field for the API key."""

        yield Static("[b $primary]galloc[/] [$muted]· providers[/]", id="header")
        with Horizontal():
            yield DataTable(cursor_type="row")
            with Vertical(id="detail"):
                yield Static(id="info")
                yield Input(id="prompt", password=True, compact=True)
                yield Message()
        yield Static(LIST_KEYS, id="keys")

    def on_mount(self) -> None:
        """Show every supported provider and focus the list."""

        table = self.query_one(DataTable)
        table.add_columns("NAME", "API KEY", "DEFAULT")
        table.focus()
        self.query_one(Input).display = False
        self._show_provider_list()

    def _show_provider_list(self) -> None:
        """Show which providers have an API key and which one is the default."""

        table = self.query_one(DataTable)
        selected_row = table.cursor_row
        table.clear()

        settings = self.app.settings
        for provider_name in PROVIDER_NAME_LIST:
            default = "yes" if provider_name == settings.provider else ""
            table.add_row(provider_name, format_status(settings, provider_name), default)
        table.move_cursor(row=selected_row)
        self._show_detail()

    def _get_selected_provider(self) -> str:
        """Return the provider under the cursor."""

        return PROVIDER_NAME_LIST[self.query_one(DataTable).cursor_row]

    def _show_detail(self) -> None:
        """Describe the selected provider."""

        self.query_one("#info", Static).update(format_provider(self.app.settings, self._get_selected_provider()))

    def on_data_table_row_highlighted(self) -> None:
        """Follow the cursor with the detail column unless it is holding the API key prompt."""

        if self.pending_provider is None:
            self._show_detail()

    def action_api_key(self) -> None:
        """Ask for the selected provider's API key beside the permissions it needs."""

        provider_name = self._get_selected_provider()
        self.pending_provider = provider_name
        self.query_one(DataTable).disabled = True
        self.query_one("#info", Static).update(
            f"[b $primary]{provider_name}[/] [$muted]· api key[/]\n\n[$muted]{API_KEY_NOTE_MAP[provider_name]}[/]"
        )
        self.query_one("#keys", Static).update(PROMPT_KEYS)

        prompt = self.query_one(Input)
        prompt.display = True
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Verify and save a non-empty key once; an empty answer keeps the prompt open."""

        api_key = event.value.strip()
        if self.busy or not api_key:
            return

        self.busy = True
        event.input.disabled = True
        self.query_one(Message).show("Checking…")
        self.save_api_key(self.pending_provider, api_key)

    @work
    async def save_api_key(self, provider_name: str, api_key: str) -> None:
        """Verify the key with the provider off the input loop, then save it; a refused key stays editable."""

        prompt = self.query_one(Input)
        try:
            settings = await asyncio.to_thread(
                operations.save_api_key, self.app.settings, self.app.config_path, provider_name, api_key
            )
        except (GallocError, OSError) as exception:
            self.busy = False
            self.query_one(Message).show(str(exception), error=True)
            prompt.disabled = False
            prompt.focus()
            return

        self.busy = False
        self.app.settings = settings
        self._close_prompt()
        self._show_provider_list()
        self.query_one(Message).show(f"Saved the {provider_name} API key")

    def action_use(self) -> None:
        """Make the selected provider the default of the SSH key screen and CLI commands, and save that choice."""

        provider_name = self._get_selected_provider()
        if not is_configured(self.app.settings, provider_name):
            self.query_one(Message).show(f"Add an API key for {provider_name} first.", error=True)
            return

        try:
            settings = operations.use_provider(self.app.settings, self.app.config_path, provider_name)
        except (GallocError, OSError) as exception:
            self.query_one(Message).show(str(exception), error=True)
            return

        self.app.settings = settings
        self._show_provider_list()
        self.query_one(Message).show(f"{provider_name} is now the default provider")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Accept nothing while a key is being verified and saved."""

        return not self.busy

    def action_back(self) -> None:
        """Close the API key prompt, or return to the GPU list."""

        if self.pending_provider is None:
            self.dismiss()
            return
        self._close_prompt()

    def _close_prompt(self) -> None:
        """Hide the API key prompt and return to the selected provider's details."""

        self.pending_provider = None

        prompt = self.query_one(Input)
        prompt.clear()
        prompt.disabled = False
        prompt.display = False

        self.query_one("#keys", Static).update(LIST_KEYS)

        table = self.query_one(DataTable)
        table.disabled = False
        table.focus()
        self._show_detail()
