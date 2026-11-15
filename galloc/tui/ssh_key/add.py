import asyncio
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static

from galloc.core.ssh_keys import generate_key_pair, read_public_key
from galloc.exceptions import GallocError
from galloc.tui import operations
from galloc.tui.message import Message
from galloc.tui.ssh_key.details import format_ssh_key


KEYS_MAP = {
    "choose": (
        "[$foreground]↑↓[/] move   [$foreground]enter[/] select   [$foreground]r[/] refresh   [$foreground]esc[/] back"
    ),
    "path": "[$foreground]enter[/] register   [$foreground]esc[/] back",
    "generate": "[$foreground]enter[/] generate   [$foreground]esc[/] back",
}
PATH_LABEL_MAP = {"path": "Public key file (.pub)", "generate": "New private key path"}
PATH_NOTE_MAP = {"path": "", "generate": "Ed25519 · no passphrase"}


class SSHKeyAddScreen(Screen[str | None]):
    """Choose an existing public key or generate a pair before registering it with the provider."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("r", "refresh", "Refresh")]

    def __init__(self) -> None:
        """Start with local key selection and no registration in progress."""

        super().__init__()
        self.mode = "choose"
        self.public_key_path_list: list[Path] = []
        self.preview_path: Path | None = None
        self.busy = False

    def compose(self) -> ComposeResult:
        """Keep choices and path entry on the left, with selected-key details on the right."""

        yield Static("[b $primary]galloc[/] [$muted]· ssh keys · add[/]", id="header")
        with Horizontal():
            with Vertical(id="key-source"):
                yield Static("Local public keys", id="path-label")
                yield OptionList(id="choice", compact=True)
                yield Input(id="key-path", compact=True)
            with Vertical(id="detail"):
                yield Static(id="info")
                yield Message()
        yield Static(KEYS_MAP["choose"], id="keys")

    def on_mount(self) -> None:
        """Discover local public key files before accepting a choice."""

        self.action_refresh()

    def action_refresh(self) -> None:
        """Reload local key choices without changing the provider or configuration."""

        self.mode = "choose"
        self.preview_path = None
        self.public_key_path_list = sorted((Path.home() / ".ssh").glob("*.pub"))
        self.query_one(Input).display = False
        self.query_one("#path-label", Static).update("Local public keys")
        self.query_one("#keys", Static).update(KEYS_MAP["choose"])

        choice = self.query_one(OptionList)
        choice.clear_options()
        choice.add_options([escape(path.name) for path in self.public_key_path_list])
        choice.add_options(["Enter public key path…", "Generate new key…"])
        choice.highlighted = 0
        choice.display = True
        choice.focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Preview the local key or describe the selected source action."""

        if event.option_index < len(self.public_key_path_list):
            self.preview_path = self.public_key_path_list[event.option_index]
            self.preview_key(self.preview_path)
        else:
            self.preview_path = None
            self.query_one("#info", Static).update("")

    @work(exclusive=True, group="preview")
    async def preview_key(self, path: Path) -> None:
        """Read and validate a local public key off the input loop."""

        try:
            ssh_key = await asyncio.to_thread(read_public_key, path)
            detail = format_ssh_key(ssh_key)
        except (GallocError, OSError) as exception:
            detail = f"[$error]{escape(str(exception))}[/]"
        if self.mode == "choose" and self.preview_path == path:
            self.query_one("#info", Static).update(detail)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Register a chosen key or ask for the path needed by the selected action."""

        if self.busy:
            return
        if event.option_index < len(self.public_key_path_list):
            self._submit(self.public_key_path_list[event.option_index])
        elif event.option_index == len(self.public_key_path_list):
            self._show_path("path", "")
        else:
            self._show_path("generate", "~/.ssh/galloc_ed25519")

    def _show_path(self, mode: str, value: str) -> None:
        """Display an explicitly labelled path field in the left pane."""

        self.mode = mode
        self.query_one(OptionList).display = False
        self.query_one("#path-label", Static).update(PATH_LABEL_MAP[mode])
        self.query_one("#info", Static).update(PATH_NOTE_MAP[mode])
        self.query_one("#keys", Static).update(KEYS_MAP[mode])

        prompt = self.query_one(Input)
        prompt.value = value
        prompt.display = True
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Keep an empty field open and submit a real path only once."""

        if self.busy:
            return
        value = event.value.strip()
        if not value:
            self.query_one(Message).show("Enter a key file path.", error=True)
            return
        self._submit(Path(value).expanduser(), generate=self.mode == "generate")

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
                self._show_path("path", str(path))
            message = await asyncio.to_thread(operations.add_ssh_key, self.app.settings, path)
        except (GallocError, OSError) as exception:
            self._show_failure(str(exception))
            return

        self.dismiss(message)

    def _show_failure(self, message: str) -> None:
        """Report the failure and let the same choice or path be submitted again."""

        self.busy = False
        self.query_one(OptionList).disabled = False
        self.query_one(Input).disabled = False
        self.query_one("#keys", Static).update(KEYS_MAP[self.mode])
        self.query_one(Message).show(message, error=True)

        if self.mode == "choose":
            self.query_one(OptionList).focus()
        else:
            self.query_one(Input).focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Keep a submission single-shot and reserve typing for path input."""

        if self.busy:
            return False
        return action != "refresh" or self.mode == "choose"

    def action_back(self) -> None:
        """Return from path entry to local choices, or close the add screen."""

        if self.mode != "choose":
            self.action_refresh()
            return
        self.dismiss(None)
