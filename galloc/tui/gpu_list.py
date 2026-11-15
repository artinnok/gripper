import asyncio
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import DataTable, Input, Static

from galloc.core.exceptions import InvalidTTL
from galloc.core.schemas import GPU
from galloc.core.ttl import calculate_expires_at, format_ttl
from galloc.exceptions import GallocError
from galloc.providers.schemas import Instance, InstanceStatus
from galloc.tui import operations
from galloc.tui.add import AddScreen
from galloc.tui.message import Message
from galloc.tui.theme import AMBER_THEME, MUTED_COLOR


REFRESH_INTERVAL = 30
LOW_TIME_LEFT = timedelta(minutes=10)
STATUS_COLOR_MAP = {
    InstanceStatus.PROVISIONING: AMBER_THEME.warning,
    InstanceStatus.RUNNING: AMBER_THEME.success,
    InstanceStatus.STOPPED: MUTED_COLOR,
    InstanceStatus.ERROR: AMBER_THEME.error,
}
LIST_KEYS = (
    "[$foreground]a[/] add   [$foreground]e[/] extend   [$foreground]d[/] delete   "
    "[$foreground]k[/] ssh key   [$foreground]r[/] refresh   [$foreground]q[/] quit"
)
PROMPT_KEYS = "[$foreground]enter[/] confirm   [$foreground]esc[/] cancel"


class GPUListScreen(Screen):
    """List GPUs beside the selected GPU's details and run actions from one prompt."""

    BINDINGS = [
        Binding("a", "add", "Add GPU"),
        Binding("e", "extend", "Extend TTL"),
        Binding("d", "delete", "Delete GPU"),
        Binding("k", "add_ssh_key", "Add SSH key"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "confirm", "Confirm"),
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self) -> None:
        """Start with an empty list and no action waiting for input."""

        super().__init__()
        self.result_list: list[tuple[GPU, Instance]] | None = []
        self.pending_action: str | None = None
        self.pending_gpu: GPU | None = None

    def compose(self) -> ComposeResult:
        """Place the GPU table beside a narrow detail column that also holds the prompt."""

        yield Static(f"[b $primary]galloc[/] [$muted]· {escape(self.app.settings.provider)}[/]", id="header")
        with Horizontal():
            yield DataTable(cursor_type="row")
            with Vertical(id="detail"):
                yield Static(id="info")
                yield Input(id="prompt", compact=True)
                yield Static(id="preview")
                yield Message()
        yield Static(LIST_KEYS, id="keys")

    def on_mount(self) -> None:
        """Load the list now and keep it current while the screen stays open."""

        self.query_one(DataTable).add_columns("NAME", "STATUS", "LEFT", "REGION", "GPU")
        self.query_one(Input).display = False
        self.refresh_gpu_list()
        self.set_interval(REFRESH_INTERVAL, self.action_refresh)

    def action_refresh(self) -> None:
        """Reload provider state unless the detail column is holding a prompt."""

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
        """Replace live rows or clear stale results after a failed refresh."""

        selected_gpu = self._get_selected_gpu()
        table = self.query_one(DataTable)
        table.clear()
        self.result_list = result_list

        now = datetime.now(UTC)
        for row, (gpu, instance) in enumerate(result_list or []):
            table.add_row(
                gpu.name,
                f"[{STATUS_COLOR_MAP[instance.status]}]{instance.status}[/]",
                _format_time_left(gpu, now),
                escape(instance.region or "-"),
                escape(instance.gpu or "-"),
            )
            if selected_gpu is not None and gpu.id == selected_gpu.id:
                table.move_cursor(row=row)

        if self.pending_action is None:
            self._show_detail()

    def on_data_table_row_highlighted(self) -> None:
        """Follow the cursor with the detail column unless a prompt is open."""

        if self.pending_action is None:
            self._show_detail()

    def _show_detail(self) -> None:
        """Describe the selected GPU using its live provider response."""

        info = self.query_one("#info", Static)
        if self.result_list is None:
            info.update("[$muted]Provider data unavailable.[/]")
            return
        if not self.result_list:
            info.update("[$muted]No GPUs.[/]")
            return

        gpu, instance = self.result_list[self.query_one(DataTable).cursor_row]
        info.update(
            f"[b $primary]{gpu.name}[/]\n"
            f"{escape(instance.gpu or '-')}\n"
            f"{escape(instance.region or '-')}\n"
            f"[{STATUS_COLOR_MAP[instance.status]}]{instance.status}[/]\n\n"
            f"[$muted]expires[/]\n{gpu.expires_at:%Y-%m-%d %H:%M} UTC\n\n"
            f"[$muted]ssh[/]\n{escape(instance.ssh_command or '-')}\n\n"
            f"[$muted]instance[/]\n{escape(instance.id)}"
        )

    def action_add(self) -> None:
        """Choose a GPU model, a region, and a TTL on their own screen."""

        self.app.push_screen(AddScreen(), self._create_gpu)

    def _create_gpu(self, selection: tuple[str, str, str] | None) -> None:
        """Create the GPU chosen on the add screen; a cancelled screen changes nothing."""

        if selection is None:
            return
        self.query_one(Message).show("Creating…")
        self.run_operation(operations.create, *selection)

    def action_extend(self) -> None:
        """Ask how much time to add to the selected GPU's deadline."""

        gpu = self._get_selected_gpu()
        if gpu is None:
            return
        self._open_prompt("extend", gpu, f"[b $primary]{gpu.name}[/] [$muted]· extend[/]", "1h")

    def action_delete(self) -> None:
        """Ask for confirmation before deleting the selected GPU."""

        gpu = self._get_selected_gpu()
        if gpu is None:
            return
        self._show_question(
            "delete", gpu, f"[b $primary]{gpu.name}[/] [$muted]· delete[/]\n\nDelete {gpu.name} and its resources?"
        )
        self.set_focus(None)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Accept only an answer while a deletion waits for confirmation."""

        if self.pending_action == "delete":
            return action in {"confirm", "cancel"}
        return action != "confirm"

    def action_confirm(self) -> None:
        """Delete the GPU named in the open confirmation."""

        gpu = self.pending_gpu
        self.action_cancel()
        self.query_one(Message).show("Deleting…")
        self.run_operation(operations.delete, gpu.id)

    def action_add_ssh_key(self) -> None:
        """Ask for the public key file to register with the configured provider."""

        self._open_prompt("ssh_key", None, "[b $primary]ssh key[/] [$muted]· add[/]", "~/.ssh/id_ed25519.pub")

    def _get_selected_gpu(self) -> GPU | None:
        """Return the GPU under the cursor, or None while the list is empty."""

        if not self.result_list:
            return None

        gpu, instance = self.result_list[self.query_one(DataTable).cursor_row]
        return gpu

    def _open_prompt(self, action: str, gpu: GPU | None, text: str, placeholder: str) -> None:
        """Ask one action's question and move input to its prompt."""

        self._show_question(action, gpu, text)

        prompt = self.query_one(Input)
        prompt.placeholder = placeholder
        prompt.display = True
        prompt.focus()

    def _show_question(self, action: str, gpu: GPU | None, text: str) -> None:
        """Replace the details with one action's question and remember what it applies to."""

        self.pending_action = action
        self.pending_gpu = gpu
        self.query_one("#info", Static).update(text)
        self.query_one("#keys", Static).update(PROMPT_KEYS)

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
        """Run the pending action with the entered value; an empty answer changes nothing."""

        action, gpu, value = self.pending_action, self.pending_gpu, event.value.strip()
        self.action_cancel()
        if not value:
            return

        if action == "extend":
            self.run_operation(operations.extend, gpu.id, value)
        elif action == "ssh_key":
            self.run_operation(operations.add_ssh_key, Path(value).expanduser())

    def action_cancel(self) -> None:
        """Close the prompt or confirmation and return to the selected GPU's details."""

        self.pending_action = None
        self.pending_gpu = None

        prompt = self.query_one(Input)
        prompt.clear()
        prompt.display = False
        self.query_one("#preview", Static).update("")
        self.query_one("#keys", Static).update(LIST_KEYS)
        self.query_one(DataTable).focus()
        self._show_detail()

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


def _format_time_left(gpu: GPU, now: datetime) -> str:
    """Show the remaining TTL, marking deadlines that are close or already passed."""

    time_left = gpu.expires_at - now
    if time_left <= timedelta(0):
        return f"[{AMBER_THEME.warning}]expired[/]"

    text = format_ttl(time_left) or "<1m"
    if time_left < LOW_TIME_LEFT:
        return f"[{AMBER_THEME.warning}]{text}[/]"
    return text
