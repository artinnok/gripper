import asyncio
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from gripper.core.ssh_keys import generate_key_pair, read_local_key_pair, read_public_key
from gripper.exceptions import GripperError
from gripper.providers.schemas import GPUModel
from gripper.tui import operations
from gripper.tui.add_steps import AddStep, format_progress
from gripper.tui.message import Message
from gripper.tui.ssh_key.access import remember_key
from gripper.tui.ssh_key.details import format_ssh_key
from gripper.tui.ssh_key.options import KeyStep, LocalKeyOption, get_local_key_option_list
from gripper.tui.wizard import WizardStep


KEY_STEPS = {
    KeyStep.CHOOSE: WizardStep(
        "Local public keys",
        "[$foreground]↑↓[/] move   [$foreground]enter[/] select   [$foreground]r[/] refresh   [$foreground]esc[/] back",
        choices=(KeyStep.PATH, KeyStep.GENERATE),
    ),
    KeyStep.PATH: WizardStep(
        "Public key file (.pub)",
        "[$foreground]enter[/] register   [$foreground]esc[/] back",
        previous_step=KeyStep.CHOOSE,
    ),
    KeyStep.GENERATE: WizardStep(
        "New private key path",
        "[$foreground]enter[/] generate   [$foreground]esc[/] back",
        previous_step=KeyStep.CHOOSE,
    ),
}
SOURCE_LABELS = {KeyStep.PATH: "Enter public key path…", KeyStep.GENERATE: "Generate new key…"}


class SSHKeyAddScreen(Screen[str | None]):
    """Register a public key, either independently or as the access step before adding a GPU."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("r", "refresh", "Refresh")]

    def __init__(self, provider_name: str | None = None, *, gpu_model: GPUModel | None = None) -> None:
        """Start with local key selection and no registration in progress."""

        super().__init__()
        self.provider_name = provider_name
        self.gpu_model = gpu_model
        self.mode = KeyStep.CHOOSE
        self.busy = False

    def compose(self) -> ComposeResult:
        """Keep choices and path entry on the left, with selected-key details on the right."""

        provider_name = self.provider_name or self.app.settings.provider
        title = "add" if self.gpu_model is not None else f"ssh keys · add to {provider_name}"
        yield Static(f"[b $primary]gripper[/] [$muted]· {escape(title)}[/]", id="header")
        if self.gpu_model is not None:
            yield Static(format_progress(AddStep.SSH_KEY, provider_name, self.gpu_model, None), id="progress")

        with Horizontal():
            with Vertical(id="key-source"):
                yield Static(KEY_STEPS[KeyStep.CHOOSE].title, id="path-label")
                yield OptionList(id="choice", compact=True)
                yield Input(id="key-path", compact=True)
            with Vertical(id="detail"):
                yield Static(id="info")
                yield Message()
        yield Static(KEY_STEPS[KeyStep.CHOOSE].keys, id="keys")

    def on_mount(self) -> None:
        """Discover local public key files before accepting a choice."""

        self.action_refresh()

    def action_refresh(self) -> None:
        """Reload local key choices without changing the provider or configuration."""

        self._show_step(KeyStep.CHOOSE)

    def _show_step(self, step: KeyStep, value: str = "") -> None:
        """Show the key sources or the selected source's path field."""

        self.mode = step
        definition = KEY_STEPS[step]
        self.query_one("#path-label", Static).update(definition.title)
        self.query_one("#keys", Static).update(definition.keys)
        if step == KeyStep.CHOOSE:
            self._show_local_keys()
        elif step == KeyStep.GENERATE:
            self._show_path_input("~/.ssh/gripper_ed25519", "Ed25519 · no passphrase")
        else:
            self._show_path_input(value)

    def _show_local_keys(self) -> None:
        """Offer each local public key, followed by the two ways to supply another key."""

        option_list = get_local_key_option_list()
        for step in KEY_STEPS[KeyStep.CHOOSE].choices:
            option_list.append(Option(SOURCE_LABELS[step], id=step))

        self.query_one(Input).display = False
        self.query_one("#info", Static).update("")
        choice = self.query_one(OptionList)
        choice.display = True
        choice.set_options(option_list)
        choice.highlighted = 0
        choice.focus()

    def _show_path_input(self, value: str, detail: str = "") -> None:
        """Focus the path field for an existing or new key."""

        self.query_one(OptionList).display = False
        self.query_one("#info", Static).update(detail)
        prompt = self.query_one(Input)
        prompt.display = True
        prompt.value = value
        prompt.focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Preview a highlighted key while the list is visible."""

        if self.mode != KeyStep.CHOOSE:
            return
        if isinstance(event.option, LocalKeyOption):
            self.preview_key(event.option)
        else:
            self.query_one("#info", Static).update("")

    @work(exclusive=True, group="preview")
    async def preview_key(self, option: LocalKeyOption) -> None:
        """Read and validate a local public key off the input loop."""

        try:
            ssh_key = await asyncio.to_thread(read_public_key, option.path)
            detail = format_ssh_key(ssh_key)
        except (GripperError, OSError) as exception:
            detail = f"[$error]{escape(str(exception))}[/]"
        if self.mode == KeyStep.CHOOSE and self.query_one(OptionList).highlighted_option is option:
            self.query_one("#info", Static).update(detail)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Register a chosen key or ask for the path needed by the selected action."""

        if self.busy:
            return
        if isinstance(event.option, LocalKeyOption):
            self._submit(event.option.path)
        else:
            self._show_step(KeyStep(event.option_id))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Keep an empty field open and submit a real path only once."""

        if self.busy:
            return
        value = event.value.strip()
        if not value:
            self.query_one(Message).show("Enter a key file path.", error=True)
            return
        self._submit(Path(value).expanduser(), generate=self.mode == KeyStep.GENERATE)

    def _submit(self, path: Path, *, generate: bool = False) -> None:
        """Disable input before starting an operation that creates files or writes provider keys."""

        self.busy = True
        self.query_one(OptionList).disabled = True
        self.query_one(Input).disabled = True
        self.query_one("#keys", Static).update("")
        self.query_one(Message).show("Registering…")
        self.register_key(path, generate=generate)

    @work
    async def register_key(self, path: Path, *, generate: bool) -> None:
        """Register a public key, keeping a generated file available if the provider refuses it."""

        try:
            if generate:
                path = await asyncio.to_thread(generate_key_pair, path)
                self._show_step(KeyStep.PATH, str(path))
            read_key = read_local_key_pair if self.gpu_model is not None else read_public_key
            key = await asyncio.to_thread(read_key, path)
            message = await asyncio.to_thread(
                operations.add_ssh_key, self.app.settings, key, provider_name=self.provider_name
            )
            if self.gpu_model is not None:
                self.app.settings = await asyncio.to_thread(
                    remember_key, self.app.settings, self.app.config_path, self.provider_name, path
                )
        except (GripperError, OSError) as exception:
            self._show_failure(str(exception))
            return

        self.dismiss(message)

    def _show_failure(self, message: str) -> None:
        """Report the failure and let the same choice or path be submitted again."""

        self.busy = False
        self.query_one(OptionList).disabled = False
        self.query_one(Input).disabled = False
        self.query_one("#keys", Static).update(KEY_STEPS[self.mode].keys)
        self.query_one(Message).show(message, error=True)

        if self.mode == KeyStep.CHOOSE:
            self.query_one(OptionList).focus()
        else:
            self.query_one(Input).focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Keep a submission single-shot and reserve typing for path input."""

        if self.busy:
            return False
        return action != "refresh" or self.mode == KeyStep.CHOOSE

    def action_back(self) -> None:
        """Return from path entry to local choices, or close the add screen."""

        previous = KEY_STEPS[self.mode].previous_step
        if previous is not None:
            self._show_step(previous)
            return
        self.dismiss(None)
