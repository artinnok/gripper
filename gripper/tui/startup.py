import asyncio
import sqlite3

from rich.spinner import Spinner
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from gripper.core.exceptions import GPUCreationPending
from gripper.core.schemas import GPU
from gripper.exceptions import GripperError
from gripper.tui import operations
from gripper.tui.message import Message


class StartupScreen(Screen[str | GripperError | None]):
    """Create one GPU and show startup progress until ready or the user stops waiting."""

    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, provider_name: str, gpu_model: str, region: str, ttl: str | None, budget: str | None) -> None:
        """Keep the selected inputs in memory until creation finishes."""

        super().__init__()
        self.provider_name = provider_name
        self.gpu_model = gpu_model
        self.provider_region = region
        self.ttl = ttl
        self.budget = budget
        self.created_gpu: GPU | None = None

    def compose(self) -> ComposeResult:
        """Use one full-screen progress view without hiding provider errors."""

        yield Static("[b $primary]gripper[/] [$muted]· add[/]", id="header")
        yield Static(id="startup")
        yield Message()
        yield Static("[$foreground]esc[/] back · submitted GPUs keep running", id="keys")

    def on_mount(self) -> None:
        """Start one billable request when this screen opens."""

        self.create_gpu()

    @work
    async def create_gpu(self) -> None:
        """Save the instance before waiting; cancellation stops polling, never deletes it."""

        progress = self.query_one("#startup", Static)
        progress.auto_refresh = 0.1
        progress.update(Spinner("arc", "Creating…"))
        try:
            gpu, instance = await asyncio.to_thread(
                operations.create,
                self.app.settings,
                self.provider_name,
                self.gpu_model,
                self.provider_region,
                self.ttl,
                self.budget,
            )
        except (GPUCreationPending, sqlite3.Error, OSError) as exception:
            # The request may have produced a GPU, so the failure stays on screen.
            self._show_failure(str(exception))
            return
        except GripperError as exception:
            # Nothing was created: the GPU is chosen again with the reason shown.
            self.dismiss(exception)
            return

        self.created_gpu = gpu
        progress.update(Spinner("arc", f"{gpu.name}  Preparing…"))
        try:
            await operations.wait_for_gpu(self.app.settings, gpu, instance)
        except (GripperError, sqlite3.Error, OSError) as exception:
            self._show_failure(f"{gpu.name}: {exception}")
            return

        self.dismiss(f"{gpu.name} ready")

    def _show_failure(self, message: str) -> None:
        """Stop the spinner and keep the failure on screen until the user leaves."""

        progress = self.query_one("#startup", Static)
        progress.auto_refresh = None
        progress.update("")
        self.query_one(Message).show(message, error=True)

    def action_cancel(self) -> None:
        """Return to the list, keeping any submitted GPU and its deadline intact."""

        message = None
        if self.created_gpu is not None:
            message = f"{self.created_gpu.name}: stopped waiting."
        self.dismiss(message)
