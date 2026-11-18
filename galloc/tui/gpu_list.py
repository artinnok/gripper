import asyncio
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import DataTable, Input, Static

from galloc.core.exceptions import InvalidTTL
from galloc.core.schemas import GPU
from galloc.core.ssh_keys import format_ssh_command
from galloc.core.ttl import calculate_expires_at
from galloc.exceptions import GallocError
from galloc.providers.exceptions import CreationRejected
from galloc.providers.schemas import Instance
from galloc.tui import operations
from galloc.tui.add import AddScreen
from galloc.tui.gpu_details import format_gpu, format_status, format_time_left
from galloc.tui.message import Message
from galloc.tui.provider.list import ProviderListScreen
from galloc.tui.ssh_key.access import configured_key_path
from galloc.tui.ssh_key.list import SSHKeyListScreen
from galloc.tui.startup import StartupScreen


REFRESH_INTERVAL = 30
COUNTDOWN_INTERVAL = 1
LIST_KEYS = (
    "[$foreground]a[/] add   [$foreground]e[/] extend   [$foreground]d[/] delete   "
    "[$foreground]c[/] copy SSH   "
    "[$foreground]k[/] ssh keys   [$foreground]p[/] providers   [$foreground]r[/] refresh   [$foreground]q[/] quit"
)
QUESTION_KEYS = "[$foreground]enter[/] confirm   [$foreground]esc[/] cancel"
QUIT_WARNING = (
    "[b $primary]Quit galloc?[/]\n\n"
    "[$warning]TTL monitoring will stop.\nGPUs will not be deleted.\nThey will remain billable.[/]"
)


class GPUListScreen(Screen):
    """List GPUs beside the selected GPU's details and ask every question in that detail column."""

    BINDINGS = [
        Binding("a", "add", "Add GPU"),
        Binding("e", "extend", "Extend TTL"),
        Binding("d", "delete", "Delete GPU"),
        Binding("c", "copy_ssh", "Copy SSH"),
        Binding("k", "ssh_keys", "SSH keys"),
        Binding("p", "providers", "Providers"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "confirm", "Confirm"),
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self) -> None:
        """Start with an empty list and no question waiting for an answer."""

        super().__init__()
        # None means the last provider read failed, so no status is shown instead of a stale one.
        self.result_list: list[tuple[GPU, Instance]] | None = []
        self.pending_action: str | None = None
        self.pending_gpu: GPU | None = None

    def compose(self) -> ComposeResult:
        """Place the GPU table beside a narrow detail column that also holds the extension prompt."""

        yield Static(id="header")
        with Horizontal():
            yield DataTable(cursor_type="row")
            with Vertical(id="detail"):
                yield Static(id="info")
                yield Input(id="prompt", placeholder="1h", compact=True)
                yield Static(id="preview")
                yield Message()
        yield Static(LIST_KEYS, id="keys")

    def on_mount(self) -> None:
        """Load the list now and keep it and its countdowns current while the screen stays open."""

        table = self.query_one(DataTable)
        for column in ("NAME", "PROVIDER", "STATUS", "LEFT", "REGION", "GPU"):
            table.add_column(column, key=column)
        table.focus()
        self.query_one(Input).display = False
        self._show_header()

        self.refresh_gpu_list()
        self.set_interval(REFRESH_INTERVAL, self.action_refresh)
        self.set_interval(COUNTDOWN_INTERVAL, self._update_time_left)

    def _show_header(self) -> None:
        """Name the default provider, which the key screen registers new SSH keys with."""

        header = f"[b $primary]galloc[/] [$muted]· {escape(self.app.settings.provider)}[/]"
        if self.app.settings.debug:
            log_path = self.app.config_path.with_name("debug.log")
            header += f" [$muted]· debug: {escape(str(log_path))}[/]"
        self.query_one("#header", Static).update(header)

    def action_refresh(self) -> None:
        """Reload provider state unless the detail column is holding a question."""

        if self.pending_action is None:
            self.refresh_gpu_list()

    @work(exclusive=True, group="refresh")
    async def refresh_gpu_list(self) -> None:
        """Read provider state off the input loop and show only the latest result."""

        try:
            result_list = await asyncio.to_thread(operations.load_gpu_list, self.app.settings)
        except (GallocError, sqlite3.Error, OSError) as exception:
            self._show_gpu_list(None)
            self.query_one(Message).show(str(exception), error=True)
            return

        self._show_gpu_list(result_list)

    def _show_gpu_list(self, result_list: list[tuple[GPU, Instance]] | None) -> None:
        """Replace the rows with a live result, or clear them after a failed read."""

        selected_gpu = self._get_selected_gpu()
        table = self.query_one(DataTable)
        table.clear()
        self.result_list = result_list

        now = datetime.now(UTC)
        for row, (gpu, instance) in enumerate(result_list or []):
            table.add_row(
                gpu.name,
                gpu.provider,
                format_status(instance),
                format_time_left(gpu, now),
                escape(instance.region or "-"),
                escape(instance.gpu or "-"),
                key=gpu.name,
            )
            if selected_gpu is not None and gpu.id == selected_gpu.id:
                table.move_cursor(row=row)

        if self.pending_action is None:
            self._show_detail()

    def _get_selected_gpu(self) -> GPU | None:
        """Return the GPU under the cursor, or None while there is nothing to select."""

        if not self.result_list:
            return None

        gpu, instance = self.result_list[self.query_one(DataTable).cursor_row]
        return gpu

    def _show_detail(self) -> None:
        """Describe the selected GPU, or say why there is nothing to describe."""

        info = self.query_one("#info", Static)
        if self.result_list is None:
            info.update("[$muted]Provider data unavailable.[/]")
        elif not self.result_list:
            info.update("[$muted]No GPUs.[/]")
        else:
            gpu, instance = self.result_list[self.query_one(DataTable).cursor_row]
            info.update(format_gpu(gpu, instance, configured_key_path(self.app.settings, gpu.provider)))

    def on_data_table_row_highlighted(self) -> None:
        """Follow the cursor with the detail column unless it is holding a question."""

        if self.pending_action is None:
            self._show_detail()

    def _update_time_left(self) -> None:
        """Tick the displayed countdowns locally without reading the provider."""

        table = self.query_one(DataTable)
        now = datetime.now(UTC)
        for gpu, instance in self.result_list or []:
            table.update_cell(gpu.name, "LEFT", format_time_left(gpu, now), update_width=True)

    def action_add(self, error: str = "") -> None:
        """Start GPU selection from scratch, optionally showing why the previous attempt was refused."""

        self.app.push_screen(AddScreen(error), self._create_gpu)

    def _create_gpu(self, selection: tuple[str, str, str, str | None, str | None] | CreationRejected | None) -> None:
        """Open startup progress for the chosen GPU; a cancelled selection creates nothing."""

        if selection is None:
            return
        if isinstance(selection, CreationRejected):
            self.action_add(str(selection))
            return

        self.app.push_screen(StartupScreen(*selection), self._show_created_gpu)

    def _show_created_gpu(self, message: str | GallocError | None) -> None:
        """Restart selection after a start that created nothing; otherwise report the outcome and reload the list."""

        if isinstance(message, GallocError):
            self.action_add(str(message))
            return

        if message is not None:
            self.query_one(Message).show(message)
        self.refresh_gpu_list()

    def action_ssh_keys(self) -> None:
        """Open the provider's SSH keys as their own list."""

        self.app.push_screen(SSHKeyListScreen())

    def action_copy_ssh(self) -> None:
        """Copy the selected GPU's complete SSH command without relying on mouse text selection."""

        if self.pending_action is not None or not self.result_list:
            return
        gpu, instance = self.result_list[self.query_one(DataTable).cursor_row]
        if not instance.ssh_command:
            self.query_one(Message).show("SSH is not available yet.", error=True)
            return

        command = format_ssh_command(instance.ssh_command, configured_key_path(self.app.settings, gpu.provider))
        self.app.copy_to_clipboard(command)
        self.query_one(Message).show("SSH command copied.")

    def action_providers(self) -> None:
        """Open provider API keys and the choice of the default provider."""

        self.app.push_screen(ProviderListScreen(), lambda result: self._show_header())

    def action_extend(self) -> None:
        """Ask how much time to add to the selected GPU's deadline."""

        gpu = self._get_selected_gpu()
        if gpu is None:
            return
        self._show_question("extend", gpu, f"[b $primary]{gpu.name}[/] [$muted]· extend[/]")

        prompt = self.query_one(Input)
        prompt.display = True
        prompt.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Preview the new deadline while a valid extension is being typed."""

        if self.pending_action != "extend":
            return

        preview = self.query_one("#preview", Static)
        try:
            expires_at = calculate_expires_at(event.value.strip(), self.pending_gpu.expires_at)
        except InvalidTTL:
            preview.update("")
            return
        preview.update(f"[$muted]new deadline[/]\n{expires_at:%Y-%m-%d %H:%M} UTC")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Extend by the entered duration; an empty answer changes nothing."""

        gpu, ttl = self.pending_gpu, event.value.strip()
        self.action_cancel()
        if ttl:
            self.run_operation(operations.extend, gpu.id, ttl)

    def action_delete(self) -> None:
        """Ask for confirmation before deleting the selected GPU."""

        gpu = self._get_selected_gpu()
        if gpu is None:
            return
        self._show_question(
            "delete", gpu, f"[b $primary]{gpu.name}[/] [$muted]· delete[/]\n\nDelete {gpu.name} and its resources?"
        )
        self.set_focus(None)

    def action_quit(self) -> None:
        """Replace any open question with the exit warning until it is confirmed or cancelled."""

        self._close_prompt()
        self.query_one(Message).update("")
        self._show_question("quit", None, QUIT_WARNING)
        self.set_focus(None)

    def _show_question(self, action: str, gpu: GPU | None, text: str) -> None:
        """Replace the details with one action's question and remember what it applies to."""

        self.pending_action = action
        self.pending_gpu = gpu
        self.query_one("#info", Static).update(text)
        self.query_one("#keys", Static).update(QUESTION_KEYS)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Accept only an answer while deletion or exit waits for confirmation."""

        if self.pending_action in {"delete", "quit"}:
            return action in {"confirm", "cancel"}
        return action != "confirm"

    def action_confirm(self) -> None:
        """Perform the action named in the open confirmation."""

        if self.pending_action == "quit":
            self.app.confirm_quit()
            return

        gpu = self.pending_gpu
        self.action_cancel()
        self.query_one(Message).show("Deleting…")
        self.run_operation(operations.delete, gpu.id)

    def action_cancel(self) -> None:
        """Close the open question and return to the selected GPU's details."""

        self.pending_action = None
        self.pending_gpu = None

        self._close_prompt()
        self.query_one("#keys", Static).update(LIST_KEYS)
        self.query_one(DataTable).focus()
        self._show_detail()

    def _close_prompt(self) -> None:
        """Hide the extension prompt together with its deadline preview."""

        prompt = self.query_one(Input)
        prompt.clear()
        prompt.display = False
        self.query_one("#preview", Static).update("")

    @work
    async def run_operation(self, operation: Callable[..., str | None], *argument_list: object) -> None:
        """Run one operation off the input loop, report its outcome, and reload the list."""

        error = False
        try:
            message = await asyncio.to_thread(operation, self.app.settings, *argument_list)
        except (GallocError, sqlite3.Error, OSError) as exception:
            message, error = str(exception), True

        if message is not None:
            self.query_one(Message).show(message, error=error)
        self.refresh_gpu_list()
