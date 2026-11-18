import asyncio

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import DataTable, Static

from galloc.core.ssh_keys import SSHKey
from galloc.exceptions import GallocError
from galloc.tui import operations
from galloc.tui.message import Message
from galloc.tui.ssh_key.add import SSHKeyAddScreen
from galloc.tui.ssh_key.details import format_ssh_key


LIST_KEYS = "[$foreground]a[/] add   [$foreground]d[/] remove   [$foreground]r[/] refresh   [$foreground]esc[/] GPUs"
CONFIRM_KEYS = "[$foreground]enter[/] confirm   [$foreground]esc[/] cancel"


class SSHKeyListScreen(Screen):
    """List every provider's SSH keys on the left and describe the selected key on the right."""

    BINDINGS = [
        Binding("a", "add", "Add SSH key"),
        Binding("d", "remove", "Remove SSH key"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "confirm", "Confirm"),
        Binding("escape", "back", "Back"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self) -> None:
        """Start with no registered keys loaded and no pending removal."""

        super().__init__()
        self.ssh_key_list: list[tuple[str, SSHKey]] = []
        self.pending_key: tuple[str, SSHKey] | None = None
        self.busy = False

    def compose(self) -> ComposeResult:
        """Reuse the GPU screen's list and detail layout for SSH key entities."""

        yield Static("[b $primary]galloc[/] [$muted]· ssh keys[/]", id="header")
        with Horizontal():
            yield DataTable(cursor_type="row")
            with Vertical(id="detail"):
                yield Static("Loading…", id="info")
                yield Message()
        yield Static(LIST_KEYS, id="keys")

    def on_mount(self) -> None:
        """Load registered keys and focus their list."""

        table = self.query_one(DataTable)
        table.add_columns("NAME", "PROVIDER", "FINGERPRINT")
        table.focus()
        self.action_refresh()

    @work(exclusive=True, group="keys")
    async def action_refresh(self) -> None:
        """Replace the list with every provider's current registrations; a failed provider leaves no stale rows."""

        selected_key = self._get_selected_key()
        ssh_key_list, error_list = await asyncio.to_thread(operations.load_ssh_key_list, self.app.settings)

        self.ssh_key_list = ssh_key_list
        table = self.query_one(DataTable)
        table.clear()
        for row, (provider_name, ssh_key) in enumerate(ssh_key_list):
            table.add_row(escape(ssh_key.comment or "Unnamed"), provider_name, escape(ssh_key.fingerprint))
            if selected_key == (provider_name, ssh_key):
                table.move_cursor(row=row)
        self.on_data_table_row_highlighted()

        if error_list:
            self.query_one(Message).show("\n".join(error_list), error=True)
        if error_list and not ssh_key_list:
            self.query_one("#info", Static).update("Provider data unavailable.")

    def _get_selected_key(self) -> tuple[str, SSHKey] | None:
        """Return the provider and its key under the cursor when the list is populated."""

        if not self.ssh_key_list:
            return None
        return self.ssh_key_list[self.query_one(DataTable).cursor_row]

    def on_data_table_row_highlighted(self) -> None:
        """Follow the selected key unless its removal is being confirmed."""

        if self.pending_key is not None:
            return
        selected_key = self._get_selected_key()
        if selected_key is None:
            self.query_one("#info", Static).update("No SSH keys registered.")
            return

        provider_name, ssh_key = selected_key
        self.query_one("#info", Static).update(format_ssh_key(ssh_key))

    def action_add(self) -> None:
        """Open local key selection as a separate full-screen view."""

        self.app.push_screen(SSHKeyAddScreen(), self._key_added)

    def _key_added(self, message: str | None) -> None:
        """Show the registration result and refresh the list."""

        if message is not None:
            self.query_one(Message).show(message)
            self.action_refresh()

    def action_remove(self) -> None:
        """Ask about the selected registration in its own detail column."""

        selected_key = self._get_selected_key()
        if selected_key is None:
            return
        self.pending_key = selected_key
        provider_name, ssh_key = selected_key
        self.query_one("#info", Static).update(
            f"{format_ssh_key(ssh_key)}\n\n[$warning]Remove from {escape(provider_name)}?[/]"
        )
        self.query_one("#keys", Static).update(CONFIRM_KEYS)
        self.set_focus(None)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Accept nothing while a removal runs, and only an answer while one waits for confirmation."""

        if self.busy:
            return False
        if self.pending_key is not None:
            return action in {"confirm", "back"}
        return action != "confirm"

    @work
    async def action_confirm(self) -> None:
        """Remove only the selected registration from its provider and report the result inline."""

        if self.busy:
            return
        self.busy = True
        self.query_one(Message).show("Removing…")

        provider_name, ssh_key = self.pending_key
        error = False
        try:
            message = await asyncio.to_thread(
                operations.remove_ssh_key, self.app.settings, provider_name, ssh_key.fingerprint
            )
        except (GallocError, OSError) as exception:
            message, error = str(exception), True

        self.busy = False
        self.query_one(Message).show(message, error=error)
        self.action_back()
        self.action_refresh()

    def action_back(self) -> None:
        """Cancel removal or return to the GPU list."""

        if self.pending_key is None:
            self.dismiss()
            return
        self.pending_key = None
        self.query_one("#keys", Static).update(LIST_KEYS)
        self.query_one(DataTable).focus()
        self.on_data_table_row_highlighted()
