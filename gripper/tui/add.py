import asyncio

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static

from gripper.core.exceptions import InvalidBudget, InvalidTTL
from gripper.exceptions import GripperError
from gripper.providers.exceptions import CreationRejected
from gripper.providers.factory import PROVIDER_NAME_LIST, is_configured
from gripper.providers.schemas import GPUModel, Region
from gripper.tui import operations
from gripper.tui.add_catalog import GPUCatalog
from gripper.tui.add_details import READY, GPUOption, format_region_list
from gripper.tui.add_status import CatalogStatus
from gripper.tui.add_steps import (
    ADD_STEPS,
    BACK_KEYS,
    LIMIT_PLACEHOLDER_MAP,
    AddStep,
    format_limit_preview,
    format_progress,
    parse_limit,
)
from gripper.tui.message import Message
from gripper.tui.ssh_key.access import find_registered_key, remember_key
from gripper.tui.ssh_key.add import SSHKeyAddScreen


class AddScreen(Screen[tuple[str, str, str, str | None, str | None] | CreationRejected | None]):
    """Collect a provider's GPU model, SSH access to it, a region, and either a TTL or compute budget."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("r", "refresh", "Refresh")]

    def __init__(self, error: str = "") -> None:
        """Start at GPU selection with nothing chosen."""

        super().__init__()
        self.error = error
        self.step: AddStep = AddStep.GPU
        self.error_list: list[str] = []
        self.region_list: list[Region] = []
        self.selected_provider: str | None = None
        self.selected_gpu_model: GPUModel | None = None
        self.selected_region: Region | None = None

    def compose(self) -> ComposeResult:
        """Stack the finished steps above the current step's widgets, beside statuses and messages."""

        yield Static("[b $primary]gripper[/] [$muted]· add[/]", id="header")
        with Horizontal():
            with Vertical():
                yield Static(id="progress")
                yield GPUCatalog()
                yield Static(id="step-columns")
                yield OptionList(id="step-choice", compact=True)
                yield Input(id="limit", compact=True)
                yield Static(id="preview")
            with Vertical(id="detail"):
                yield CatalogStatus()
                yield Message()
        yield Static(id="keys")

    def on_mount(self) -> None:
        """Open GPU selection and ask for the catalogs."""

        self._show_step(AddStep.GPU)
        self.action_refresh()
        if self.error:
            self.query_one(Message).show(self.error, error=True)

    def action_refresh(self) -> None:
        """Ask every provider that has an API key for a fresh catalog, all at the same time; retry an SSH check."""

        if self.query_one(GPUCatalog).disabled:
            return
        if self.step == AddStep.SSH_KEY:
            self.check_ssh_key()
            return
        if self.step != AddStep.GPU or self.query_one(CatalogStatus).loading:
            return

        provider_name_list = [name for name in PROVIDER_NAME_LIST if is_configured(self.app.settings, name)]
        self.error_list = []
        self.query_one(CatalogStatus).start(provider_name_list)
        self.query_one(GPUCatalog).clear()
        if not provider_name_list:
            self.query_one(Message).show("No provider has an API key. Press p in the GPU list to add one.", error=True)
        for provider_name in provider_name_list:
            self.load_gpu_model_list(provider_name)

    @work
    async def load_gpu_model_list(self, provider_name: str) -> None:
        """Read one provider's catalog off the input loop and show it as soon as it arrives."""

        try:
            gpu_model_list = await asyncio.to_thread(operations.load_gpu_model_list, self.app.settings, provider_name)
        except (GripperError, OSError) as exception:
            self.query_one(CatalogStatus).set_status(provider_name, "failed")
            self.error_list.append(f"{provider_name}: {exception}")
            self.query_one(Message).show("\n".join(self.error_list), error=True)
            return

        self.query_one(CatalogStatus).set_status(provider_name, READY if gpu_model_list else "no GPUs")
        self.query_one(GPUCatalog).add_gpu_model_list(provider_name, gpu_model_list)

    @work(exclusive=True, group="ssh-key")
    async def check_ssh_key(self) -> None:
        """Require access at the selected provider before asking for a region or a billable duration."""

        self.query_one(GPUCatalog).disabled = True
        self.query_one("#keys", Static).update(BACK_KEYS)
        self.query_one(CatalogStatus).show_activity("checking SSH key")
        try:
            path = await asyncio.to_thread(find_registered_key, self.app.settings, self.selected_provider)
            if path is not None:
                self.app.settings = await asyncio.to_thread(
                    remember_key, self.app.settings, self.app.config_path, self.selected_provider, path
                )
        except (GripperError, OSError) as exception:
            self._show_step(AddStep.SSH_KEY)
            self.query_one(Message).show(str(exception), error=True)
            return

        if path is None:
            self.query_one(CatalogStatus).show_activity(None)
            self.app.push_screen(
                SSHKeyAddScreen(self.selected_provider, gpu_model=self.selected_gpu_model), self._ssh_key_added
            )
            return
        self._show_step(ADD_STEPS[self.step].next_step)

    def _ssh_key_added(self, message: str | None) -> None:
        """Continue after registration, or return to GPU selection when key setup is cancelled."""

        if message is None:
            self._show_step(AddStep.GPU)
            return
        self._show_step(ADD_STEPS[self.step].next_step)

    @work(group="region")
    async def load_region_list(self) -> None:
        """Read the chosen model's regions; sold-out stock restarts selection with a fresh catalog."""

        self.query_one(CatalogStatus).show_activity("loading regions")
        try:
            self.region_list = await asyncio.to_thread(
                operations.load_region_list, self.app.settings, self.selected_provider, self.selected_gpu_model.id
            )
        except CreationRejected as exception:
            self.dismiss(exception)
            return
        except (GripperError, OSError) as exception:
            self._show_step(AddStep.GPU)
            self.query_one(Message).show(str(exception), error=True)
            return

        self.query_one(CatalogStatus).show_activity(None)
        self._show_regions()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Accept the offer, region, or limit chosen on the current step."""

        if self.step == AddStep.GPU and isinstance(event.option, GPUOption):
            self.selected_provider = event.option.provider_name
            self.selected_gpu_model = event.option.gpu_model
            self.check_ssh_key()

        elif self.step == AddStep.REGION and event.option_list.id == "step-choice":
            self.selected_region = self.region_list[event.option_index]
            self._show_step(ADD_STEPS[self.step].next_step)

        elif self.step == AddStep.LIMIT and event.option_list.id == "step-choice":
            self._show_step(ADD_STEPS[self.step].choices[event.option_index])

    def on_input_changed(self, event: Input.Changed) -> None:
        """Preview the cost of the entered TTL or the duration the entered budget buys."""

        preview = format_limit_preview(self.step, event.value.strip(), self.selected_gpu_model.hourly_price)
        self.query_one("#preview", Static).update(preview)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Hand a validated limit to startup for final provider validation."""

        value = event.value.strip()
        try:
            parse_limit(self.step, value, self.selected_gpu_model.hourly_price)
        except (InvalidTTL, InvalidBudget) as exception:
            self.query_one(Message).show(str(exception), error=True)
            return

        ttl = value if self.step == AddStep.TTL else None
        budget = value if self.step == AddStep.BUDGET else None
        self.dismiss((self.selected_provider, self.selected_gpu_model.id, self.selected_region.id, ttl, budget))

    def action_back(self) -> None:
        """Return to the previous step with the cursor on its answer; the first step closes search, then the screen."""

        catalog = self.query_one(GPUCatalog)
        previous = AddStep.GPU if catalog.disabled else ADD_STEPS[self.step].previous_step
        if previous == AddStep.GPU:
            self.workers.cancel_group(self, "ssh-key")
            self.workers.cancel_group(self, "region")

        if previous is not None:
            self._show_step(previous)
        elif catalog.searching:
            catalog.close_search()
        else:
            self.dismiss(None)

    def _show_step(self, step: AddStep) -> None:
        """Make a step current: list the answered steps above it, name its keys, and hide other steps' widgets."""

        previous = self.step
        self.step = step
        self._reset_step_widgets(step)

        definition = ADD_STEPS[step]
        if step == AddStep.GPU:
            selected = None
            if self.selected_gpu_model is not None:
                selected = self.selected_provider, self.selected_gpu_model
            self.query_one(GPUCatalog).show(selected)
        elif step == AddStep.REGION and ADD_STEPS[previous].previous_step == step:
            self._show_regions()
        elif step == AddStep.REGION:
            self.selected_region = None
            self.load_region_list()
        elif definition.choices:
            highlighted = definition.choices.index(previous) if previous in definition.choices else 0
            self._show_choice([ADD_STEPS[choice].title for choice in definition.choices], highlighted)
        elif step in LIMIT_PLACEHOLDER_MAP:
            self._show_limit_input(step)

    def _reset_step_widgets(self, step: AddStep) -> None:
        """Clear the previous step's inputs and activity, then update its progress and shortcuts."""

        catalog = self.query_one(GPUCatalog)
        catalog.disabled = False
        catalog.display = step == AddStep.GPU
        for selector in ("#step-columns", "#step-choice", "#limit"):
            self.query_one(selector).display = False

        self.query_one("#preview", Static).update("")
        self.query_one("#progress", Static).update(
            format_progress(step, self.selected_provider, self.selected_gpu_model, self.selected_region)
        )
        self.query_one("#keys", Static).update(ADD_STEPS[step].keys)
        self.query_one(CatalogStatus).show_activity(None)

    def _show_regions(self) -> None:
        """Restore the selected region when stepping back from its limit."""

        columns, row_list = format_region_list(self.region_list)
        highlighted = self.region_list.index(self.selected_region) if self.selected_region is not None else 0
        self._show_choice(row_list, highlighted, columns)

    def _show_choice(self, row_list: list[str], highlighted: int, columns: str = "") -> None:
        """Focus the current step's list with its header and highlighted choice."""

        header = self.query_one("#step-columns", Static)
        header.update(columns)
        header.display = bool(columns)

        choice = self.query_one("#step-choice", OptionList)
        choice.set_options(row_list)
        choice.highlighted = highlighted
        choice.display = True
        choice.focus()

    def _show_limit_input(self, step: AddStep) -> None:
        """Open the step that takes the chosen kind of limit as text, with its input format."""

        limit_input = self.query_one("#limit", Input)
        limit_input.clear()
        limit_input.placeholder = LIMIT_PLACEHOLDER_MAP[step]
        limit_input.display = True
        limit_input.focus()
