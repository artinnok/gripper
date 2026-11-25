import asyncio
from enum import StrEnum
from pathlib import Path

from pydantic import SecretStr, ValidationError
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from gripper.core.ssh_keys import generate_key_pair
from gripper.exceptions import GripperError
from gripper.providers.factory import PROVIDER_NAME_LIST
from gripper.providers.schemas import ProviderName
from gripper.settings import Settings
from gripper.tui import operations
from gripper.tui.message import Message
from gripper.tui.provider.details import API_KEY_NOTE_MAP, ProviderKeyLink
from gripper.tui.ssh_key.options import KeyStep, LocalKeyOption, get_local_key_option_list
from gripper.tui.wizard import WizardStep, render_progress


class SetupStep(StrEnum):
    """Name the steps of the first-run configuration flow."""

    PROVIDER = "provider"
    API_KEY = "api_key"
    DISK = "disk"
    SSH_KEY = "ssh_key"
    KEY_PATH = "key_path"


SETUP_STEPS = {
    SetupStep.PROVIDER: WizardStep("Provider", next_step=SetupStep.API_KEY),
    SetupStep.API_KEY: WizardStep("API key", next_step=SetupStep.DISK),
    SetupStep.DISK: WizardStep("Disk", next_step=SetupStep.SSH_KEY),
    SetupStep.SSH_KEY: WizardStep("SSH key", next_step=SetupStep.KEY_PATH),
    SetupStep.KEY_PATH: WizardStep("SSH key", previous_step=SetupStep.SSH_KEY),
}


class SetupScreen(Screen[Settings]):
    """Collect provider credentials, disk size, and an SSH key before the first GPU list."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("f2", "open_provider", "Open API keys")]

    def __init__(self) -> None:
        """Start at provider selection with nothing confirmed or saved."""

        super().__init__()
        self.step: SetupStep = SetupStep.PROVIDER
        self.provider_name = ""
        self.api_key = ""
        self.confirmed_settings: Settings | None = None
        self.busy = False

    def compose(self) -> ComposeResult:
        """Stack the finished steps above the one widget that takes the current answer."""

        yield Static("[b $primary]gripper setup[/]", id="header")
        yield Static(id="progress")
        yield OptionList(*PROVIDER_NAME_LIST, id="choice", compact=True)
        yield Input(id="prompt", compact=True)
        yield Static(id="note")
        yield ProviderKeyLink()
        yield Message()

    def on_mount(self) -> None:
        """Begin with the provider list as the only active widget."""

        self._show_step(SetupStep.PROVIDER)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Take the provider first; the same list later takes the SSH key choice."""

        if self.busy:
            return

        if self.step == SetupStep.PROVIDER:
            self.provider_name = PROVIDER_NAME_LIST[event.option_index]
            self._show_step(SETUP_STEPS[self.step].next_step)
        elif isinstance(event.option, LocalKeyOption):
            self._submit(event.option.path)
        elif event.option_id == KeyStep.PATH:
            self._show_step(SETUP_STEPS[self.step].next_step)
        elif event.option_id == KeyStep.GENERATE:
            self._submit(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Validate the current field before advancing or registering a key."""

        if self.busy:
            return

        value = event.value.strip()
        if self.step == SetupStep.API_KEY and value:
            event.input.disabled = True
            self.check_api_key(value)
        elif self.step == SetupStep.DISK:
            self._accept_disk(value)
        elif self.step == SetupStep.KEY_PATH and value:
            self._submit(Path(value).expanduser())
        elif self.step == SetupStep.KEY_PATH:
            self._show_failure("Enter a public key file path.")

    def action_open_provider(self) -> None:
        """Open the chosen provider in the browser while entering its API key."""

        if self.step == SetupStep.API_KEY:
            self.query_one(ProviderKeyLink).action_open_link()

    @work
    async def check_api_key(self, api_key: str) -> None:
        """Verify the key off the input loop; a refused key stays editable."""

        settings = self.app.settings.model_copy(
            update={"provider": self.provider_name, f"{self.provider_name}_api_key": SecretStr(api_key)}
        )
        try:
            await asyncio.to_thread(operations.check_api_key, settings)
        except (GripperError, OSError) as exception:
            self._show_failure(str(exception))
            return

        self.confirmed_settings = settings
        self.api_key = api_key
        self.query_one(Input).disabled = False
        self._show_step(SETUP_STEPS[self.step].next_step)

    def _accept_disk(self, value: str) -> None:
        """Apply the shared settings validation before any SSH registration or config write."""

        try:
            self.confirmed_settings = Settings.model_validate(self.confirmed_settings.model_dump() | {"disk_gb": value})
        except ValidationError:
            self._show_failure("Enter a positive whole number of GB.")
            return

        self._show_step(SETUP_STEPS[self.step].next_step)

    def _show_step(self, step: SetupStep, value: str = "") -> None:
        """Render the requested field and the answers already confirmed in setup."""

        self.step = step
        self._show_progress(step)
        self._show_note(step)

        choice = self.query_one(OptionList)
        prompt = self.query_one(Input)
        choice.display = step in {SetupStep.PROVIDER, SetupStep.SSH_KEY}
        prompt.display = not choice.display
        if step == SetupStep.SSH_KEY:
            self._load_key_choices(choice)
        if choice.display:
            choice.focus()
            return

        if step == SetupStep.DISK:
            value = str(self.confirmed_settings.disk_gb)
        prompt.password = step == SetupStep.API_KEY
        prompt.value = value
        prompt.focus()

    def _show_progress(self, step: SetupStep) -> None:
        """Display completed answers and the current question, masking the API key."""

        answers = {SetupStep.PROVIDER: self.provider_name}
        if self.confirmed_settings is not None:
            answers[SetupStep.API_KEY] = "••••"
            answers[SetupStep.DISK] = f"{self.confirmed_settings.disk_gb} GB"
        title = None
        if step == SetupStep.DISK:
            title = "Minimum disk (GiB)" if self.provider_name == ProviderName.LAMBDA else "Disk (GB)"
        self.query_one("#progress", Static).update(render_progress(SETUP_STEPS, step, answers, title=title))

    def _show_note(self, step: SetupStep) -> None:
        """Show provider permissions or the expected public key file type when relevant."""

        note = ""
        link = self.query_one(ProviderKeyLink)
        link.display = False
        if step == SetupStep.API_KEY:
            note = API_KEY_NOTE_MAP[self.provider_name]
            link.show_provider(self.provider_name)
        elif step == SetupStep.KEY_PATH:
            note = "Public key file (.pub)"
        self.query_one("#note", Static).update(note)

    def _load_key_choices(self, choice: OptionList) -> None:
        """Offer discovered keys, manual path entry and key generation."""

        option_list = get_local_key_option_list(show_directory=True)
        option_list.append(Option("Enter public key path…", id=KeyStep.PATH))
        option_list.append(Option("Generate new key", id=KeyStep.GENERATE))
        choice.set_options(option_list)
        choice.highlighted = 0

    def _submit(self, public_key_path: Path | None) -> None:
        """Lock the screen and finish setup once; no path asks for a new key pair."""

        self.busy = True
        self.query_one(OptionList).disabled = True
        self.query_one(Input).disabled = True
        self.query_one(Message).show("Saving setup…")
        self.finish_setup(public_key_path)

    @work
    async def finish_setup(self, public_key_path: Path | None) -> None:
        """Register the key and save the configuration, then hand the settings to the app."""

        try:
            if public_key_path is None:
                public_key_path = await asyncio.to_thread(generate_key_pair, Path.home() / ".ssh" / "gripper_ed25519")
                # A refused registration then retries this key instead of generating another one.
                self._show_step(SetupStep.KEY_PATH, str(public_key_path))
            self.confirmed_settings = await asyncio.to_thread(
                operations.finish_setup, self.confirmed_settings, self.app.config_path, self.api_key, public_key_path
            )
        except (GripperError, OSError) as exception:
            self._show_failure(str(exception))
            return

        self.dismiss(self.confirmed_settings)

    def _show_failure(self, message: str) -> None:
        """Report the failure, unlock the screen, and let the current step be answered again."""

        self.busy = False
        self.query_one(Message).show(message, error=True)
        self.query_one(OptionList).disabled = False
        self.query_one(Input).disabled = False

        if self.step == SetupStep.SSH_KEY:
            self.query_one(OptionList).focus()
        else:
            self.query_one(Input).focus()

    def action_back(self) -> None:
        """Return from a custom path to the key choices; from any other step leave setup."""

        previous = SETUP_STEPS[self.step].previous_step
        if previous is not None and not self.busy:
            self._show_step(previous)
            return
        self.action_quit()

    def action_quit(self) -> None:
        """Leave without saving, unless a submission is still registering the key and writing the file."""

        if self.busy:
            self.query_one(Message).show("Finishing setup…")
            return
        self.app.exit()
