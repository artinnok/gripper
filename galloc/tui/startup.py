import asyncio
import sqlite3

from rich.spinner import Spinner
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from galloc.core.schemas import GPU
from galloc.exceptions import GallocError
from galloc.providers.exceptions import CreationRejected
from galloc.tui import operations
from galloc.tui.message import Message


class StartupScreen(Screen[str | CreationRejected | None]):
    """Create one GPU and show startup progress until ready or the user stops waiting."""

    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, gpu_model: str, region: str, ttl: str | None, budget: str | None) -> None:
        """Keep the selected inputs in memory until creation finishes."""

        super().__init__()
        self.gpu_model = gpu_model
        self.provider_region = region
        self.ttl = ttl
        self.budget = budget
        self.created_gpu: GPU | None = None

    def compose(self) -> ComposeResult:
        """Use one full-screen progress view without hiding provider errors."""

        yield Static("[b $primary]galloc[/] [$muted]· add[/]", id="header")
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
                operations.create, self.app.settings, self.gpu_model, self.provider_region, self.ttl, self.budget
            )
            self.created_gpu = gpu
            progress.update(Spinner("arc", f"{gpu.name}  Preparing…"))
            await operations.wait_for_gpu(self.app.settings, gpu, instance)
        except CreationRejected as exception:
            self.dismiss(exception)
            return
        except (GallocError, sqlite3.Error, OSError) as exception:
            prefix = f"{self.created_gpu.name}: " if self.created_gpu is not None else ""
            progress.update("")
            self.query_one(Message).show(f"{prefix}{exception}", error=True)
            return
        finally:
            progress.auto_refresh = None

        self.dismiss(f"{gpu.name} ready")

    def action_cancel(self) -> None:
        """Return to the list, keeping any submitted GPU and its deadline intact."""

        message = None
        if self.created_gpu is not None:
            message = f"{self.created_gpu.name}: stopped waiting."
        self.dismiss(message)
