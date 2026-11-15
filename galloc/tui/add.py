import asyncio
from datetime import timedelta
from decimal import Decimal

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static

from galloc.core.exceptions import InvalidBudget, InvalidTTL
from galloc.core.pricing import calculate_budget_ttl, calculate_cost, parse_budget
from galloc.core.ttl import format_ttl, parse_ttl
from galloc.exceptions import GallocError
from galloc.providers.exceptions import CreationRejected
from galloc.providers.schemas import GPUModel, Region
from galloc.tui import operations
from galloc.tui.message import Message
from galloc.tui.theme import CURRENT, DONE


CHOICE_KEYS = "[$foreground]↑↓[/] move   [$foreground]enter[/] select   [$foreground]esc[/] cancel"
GPU_KEYS = f"{CHOICE_KEYS}   [$foreground]r[/] refresh"
LIMIT_KEYS = "[$foreground]enter[/] create   [$foreground]esc[/] cancel"
LIMIT_LABEL_MAP = {"ttl": "TTL", "budget": "Budget (USD)"}
LIMIT_PLACEHOLDER_MAP = {"ttl": "30m, 1h, 1d", "budget": "2.5"}
LOW_PRICE_THRESHOLD = 0.40
PRICE_COLUMN_WIDTH = 9
MEMORY_COLUMN_WIDTH = 9
REGION_COLUMN_WIDTH = 14


class AddScreen(Screen[tuple[str, str, str | None, str | None] | CreationRejected | None]):
    """Collect a GPU model, a region, and either a TTL or compute budget."""

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("r", "refresh", "Refresh")]

    def __init__(self, error: str = "") -> None:
        """Start at GPU selection with nothing chosen."""

        super().__init__()
        self.error = error
        self.step = "gpu"
        self.gpu_model_list: list[GPUModel] = []
        self.region_list: list[Region] = []
        self.selected_gpu_model: GPUModel | None = None
        self.selected_region: Region | None = None

    def compose(self) -> ComposeResult:
        """Stack the finished steps above the one widget that takes the current answer."""

        yield Static("[b $primary]galloc[/] [$muted]· add[/]", id="header")
        yield Static(f"{CURRENT} GPU", id="progress")
        yield Static(id="columns")
        yield OptionList(id="choice", compact=True)
        yield Input(id="limit", compact=True)
        yield Static("Loading…", id="note")
        yield Message()
        yield Static(GPU_KEYS, id="keys")

    def on_mount(self) -> None:
        """Read the catalog before anything can be chosen."""

        self.query_one(Input).display = False
        self.query_one("#columns", Static).display = False
        if self.error:
            self.query_one(Message).show(self.error, error=True)
        self.action_refresh()

    def action_refresh(self) -> None:
        """Replace the GPU catalog with a fresh provider response."""

        choice = self.query_one(OptionList)
        if self.step != "gpu" or choice.disabled:
            return

        choice.disabled = True
        choice.clear_options()
        self.query_one("#note", Static).update("Loading…")
        self.load_gpu_model_list()

    @work
    async def load_gpu_model_list(self) -> None:
        """Read the catalog off the input loop; a failed or empty read waits for a manual refresh."""

        try:
            gpu_model_list = await asyncio.to_thread(operations.load_gpu_model_list, self.app.settings)
        except (GallocError, OSError) as exception:
            self._show_failure(str(exception))
            return

        self._show_gpu_step(gpu_model_list)

    def _show_gpu_step(self, gpu_model_list: list[GPUModel]) -> None:
        """Offer the available GPU models with their hourly price, memory, and stock."""

        self.gpu_model_list = gpu_model_list
        self.query_one("#note", Static).update("")
        name_width = max(len("GPU"), max(len(gpu_model.name) for gpu_model in gpu_model_list))
        columns = self.query_one("#columns", Static)
        columns.update(
            f"{'GPU':<{name_width}}     {'USD/HOUR':<{PRICE_COLUMN_WIDTH}}  {'VRAM (GB)':<{MEMORY_COLUMN_WIDTH}}  STOCK"
        )
        columns.display = True

        choice = self.query_one(OptionList)
        choice.add_options([_format_gpu_model(gpu_model, name_width) for gpu_model in gpu_model_list])
        choice.highlighted = 0
        choice.disabled = False
        choice.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Take the GPU model first; the same list then takes the region and the kind of limit."""

        if self.step == "gpu":
            self.selected_gpu_model = self.gpu_model_list[event.option_index]
            event.option_list.disabled = True
            self.load_region_list()
        elif self.step == "region":
            self._show_limit_step(self.region_list[event.option_index])
        else:
            self._show_limit_input(list(LIMIT_LABEL_MAP)[event.option_index])

    @work
    async def load_region_list(self) -> None:
        """Read the chosen model's regions; sold-out stock restarts selection with a fresh catalog."""

        try:
            region_list = await asyncio.to_thread(
                operations.load_region_list, self.app.settings, self.selected_gpu_model.id
            )
        except CreationRejected as exception:
            self.dismiss(exception)
            return
        except (GallocError, OSError) as exception:
            self._show_failure(str(exception))
            return

        self._show_region_step(region_list)

    def _show_region_step(self, region_list: list[Region]) -> None:
        """Offer the regions that have the chosen GPU model in stock."""

        self.step = "region"
        self.region_list = region_list
        self.query_one("#progress", Static).update(f"{self._describe_gpu_model()}\n{CURRENT} Region")
        self.query_one("#keys", Static).update(CHOICE_KEYS)
        self.query_one("#columns", Static).update(f"{'ID':<{REGION_COLUMN_WIDTH}}  LOCATION")

        choice = self.query_one(OptionList)
        choice.clear_options()
        choice.add_options([escape(f"{region.id:<{REGION_COLUMN_WIDTH}}  {region.location}") for region in region_list])
        choice.highlighted = 0
        choice.disabled = False
        choice.focus()

    def _show_limit_step(self, region: Region) -> None:
        """Offer an explicit choice between duration and compute budget."""

        self.step = "limit"
        self.selected_region = region
        self.query_one("#progress", Static).update(
            f"{self._describe_gpu_model()}\n{DONE} Region   {escape(region.id)}\n{CURRENT} TTL or budget"
        )
        self.query_one("#columns", Static).display = False
        choice = self.query_one(OptionList)
        choice.styles.margin = (1, 0)
        choice.clear_options()
        choice.add_options(list(LIMIT_LABEL_MAP.values()))
        choice.highlighted = 0

    def _show_limit_input(self, mode: str) -> None:
        """Ask for the selected limit with its input format."""

        self.step = mode
        self.query_one("#progress", Static).update(
            f"{self._describe_gpu_model()}\n{DONE} Region   {escape(self.selected_region.id)}\n"
            f"{CURRENT} {LIMIT_LABEL_MAP[mode]}"
        )
        self.query_one("#keys", Static).update(LIMIT_KEYS)
        self.query_one(OptionList).display = False

        limit_input = self.query_one(Input)
        limit_input.placeholder = LIMIT_PLACEHOLDER_MAP[mode]
        limit_input.display = True
        limit_input.focus()

    def _describe_gpu_model(self) -> str:
        """Show the chosen GPU model as a finished step with its hourly price."""

        return (
            f"{DONE} GPU      {escape(self.selected_gpu_model.name)}  ${self.selected_gpu_model.hourly_price:.2f}/hour"
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        """Preview cost for a TTL or duration for a fractional compute budget."""

        note = self.query_one("#note", Static)
        try:
            duration = self._duration(event.value.strip())
        except InvalidTTL, InvalidBudget:
            note.update("")
            return

        if self.step == "budget":
            note.update(f"Estimated TTL: {format_ttl(duration)} · not a spending cap")
        else:
            cost = calculate_cost(duration, Decimal(str(self.selected_gpu_model.hourly_price)))
            note.update(f"about ${cost:.2f} · compute only")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Hand one validated limit to the startup screen for final provider validation."""

        value = event.value.strip()
        try:
            self._duration(value)
        except (InvalidTTL, InvalidBudget) as exception:
            self.query_one(Message).show(str(exception), error=True)
            return

        ttl = value if self.step == "ttl" else None
        budget = value if self.step == "budget" else None
        self.dismiss((self.selected_gpu_model.id, self.selected_region.id, ttl, budget))

    def _duration(self, value: str) -> timedelta:
        """Validate the entered limit using the shared CLI pricing and TTL rules."""

        if self.step == "ttl":
            return parse_ttl(value)
        price = Decimal(str(self.selected_gpu_model.hourly_price))
        return parse_ttl(calculate_budget_ttl(parse_budget(value), price))

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
    """Align catalog columns and mark GPUs priced below the low-price threshold."""

    price = f"${gpu_model.hourly_price:.2f}"
    marker = "ϟ" if gpu_model.hourly_price < LOW_PRICE_THRESHOLD else " "
    return escape(
        f"{gpu_model.name:<{name_width}}  {marker}  {price:<{PRICE_COLUMN_WIDTH}}  "
        f"{gpu_model.memory_gb:<{MEMORY_COLUMN_WIDTH}}  {gpu_model.availability.title()}"
    )
