import asyncio
from collections.abc import Callable
from decimal import Decimal

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static

from galloc.core.exceptions import InvalidTTL
from galloc.core.pricing import calculate_cost
from galloc.core.ttl import parse_ttl
from galloc.exceptions import GallocError
from galloc.providers.schemas import GPUModel, Region
from galloc.tui import operations
from galloc.tui.message import Message
from galloc.tui.theme import CURRENT, DONE


CHOICE_KEYS = "[$foreground]↑↓[/] move   [$foreground]enter[/] select   [$foreground]esc[/] cancel"
TTL_KEYS = "[$foreground]enter[/] create   [$foreground]esc[/] cancel"


class AddScreen(Screen[tuple[str, str, str] | None]):
    """Collect a GPU model, a region, and a TTL for one new GPU."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self) -> None:
        """Start at GPU selection with nothing chosen."""

        super().__init__()
        self.step = "gpu"
        self.gpu_model_list: list[GPUModel] = []
        self.region_list: list[Region] = []
        self.selected_gpu_model: GPUModel | None = None
        self.selected_region: Region | None = None

    def compose(self) -> ComposeResult:
        """Stack the finished steps above the one widget that takes the current answer."""

        yield Static("[b $primary]galloc[/] [$muted]· add[/]", id="header")
        yield Static(f"{CURRENT} GPU", id="progress")
        yield OptionList(id="choice", compact=True)
        yield Input(id="ttl", placeholder="1h", compact=True)
        yield Static("Loading…", id="note")
        yield Message()
        yield Static(CHOICE_KEYS, id="keys")

    def on_mount(self) -> None:
        """Read the catalog before anything can be chosen."""

        self.query_one(Input).display = False
        self.load(operations.load_gpu_model_list, self._show_gpu_step)

    @work
    async def load(self, operation: Callable[..., list], show: Callable[[list], None], *argument_list: object) -> None:
        """Read from the provider off the input loop, then show the result or the failure."""

        try:
            result = await asyncio.to_thread(operation, self.app.settings, *argument_list)
        except (GallocError, OSError) as exception:
            self._show_failure(str(exception))
            return

        show(result)

    def _show_gpu_step(self, gpu_model_list: list[GPUModel]) -> None:
        """Offer the available GPU models with their hourly price, memory, and stock."""

        self.gpu_model_list = gpu_model_list
        self.query_one("#note", Static).update("")
        name_width = max(len(gpu_model.name) for gpu_model in gpu_model_list)

        choice = self.query_one(OptionList)
        choice.add_options([_format_gpu_model(gpu_model, name_width) for gpu_model in gpu_model_list])
        choice.highlighted = 0
        choice.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Take the GPU model first; the same list then takes the region."""

        if self.step == "gpu":
            self.selected_gpu_model = self.gpu_model_list[event.option_index]
            event.option_list.disabled = True
            self.load(operations.load_region_list, self._show_region_step, self.selected_gpu_model.id)
            return

        self._show_ttl_step(self.region_list[event.option_index])

    def _show_region_step(self, region_list: list[Region]) -> None:
        """Offer the regions that have the chosen GPU model in stock."""

        self.step = "region"
        self.region_list = region_list
        self.query_one("#progress", Static).update(f"{self._describe_gpu_model()}\n{CURRENT} Region")

        choice = self.query_one(OptionList)
        choice.clear_options()
        choice.add_options([escape(f"{region.id:<14}  {region.location}") for region in region_list])
        choice.highlighted = 0
        choice.disabled = False
        choice.focus()

    def _show_ttl_step(self, region: Region) -> None:
        """Ask how long the GPU should live."""

        self.step = "ttl"
        self.selected_region = region
        self.query_one("#progress", Static).update(
            f"{self._describe_gpu_model()}\n{DONE} Region   {escape(region.id)}\n{CURRENT} TTL"
        )
        self.query_one("#keys", Static).update(TTL_KEYS)
        self.query_one(OptionList).display = False

        ttl_input = self.query_one(Input)
        ttl_input.display = True
        ttl_input.focus()

    def _describe_gpu_model(self) -> str:
        """Show the chosen GPU model as a finished step with its hourly price."""

        return (
            f"{DONE} GPU      {escape(self.selected_gpu_model.name)}  ${self.selected_gpu_model.hourly_price:.2f}/hour"
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        """Estimate the compute cost while a valid TTL is being typed."""

        note = self.query_one("#note", Static)
        try:
            duration = parse_ttl(event.value.strip())
        except InvalidTTL:
            note.update("")
            return

        cost = calculate_cost(duration, Decimal(str(self.selected_gpu_model.hourly_price)))
        note.update(f"about ${cost:.2f}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Hand a valid selection to the GPU list, which creates the GPU."""

        ttl = event.value.strip()
        try:
            parse_ttl(ttl)
        except InvalidTTL as exception:
            self.query_one(Message).show(str(exception), error=True)
            return

        self.dismiss((self.selected_gpu_model.id, self.selected_region.id, ttl))

    def action_cancel(self) -> None:
        """Return to the GPU list without creating anything."""

        self.dismiss(None)

    def _show_failure(self, message: str) -> None:
        """Report the failure and let the GPU model be chosen again."""

        self.query_one(Message).show(message, error=True)
        self.query_one("#note", Static).update("")

        choice = self.query_one(OptionList)
        choice.disabled = False
        choice.focus()


def _format_gpu_model(gpu_model: GPUModel, name_width: int) -> str:
    """Line up one catalog row: name, hourly price, GPU memory, and stock."""

    price = f"${gpu_model.hourly_price:.2f}"
    return escape(
        f"{gpu_model.name:<{name_width}}  {price:>6}/hour  {gpu_model.memory_gb:>3} GB  "
        f"{gpu_model.availability.lower()} stock"
    )
