from enum import StrEnum
from pathlib import Path

from textual.markup import escape
from textual.widgets.option_list import Option


class KeyStep(StrEnum):
    """Name the local SSH key source and its two path prompts."""

    CHOOSE = "choose"
    PATH = "path"
    GENERATE = "generate"


class LocalKeyOption(Option):
    """Keep the public key path with the row that displays it."""

    def __init__(self, path: Path, label: str) -> None:
        """Display the path or filename used by the current screen."""

        super().__init__(escape(label))
        self.path = path


def get_local_key_option_list(*, show_directory: bool = False) -> list[Option]:
    """List the user's public keys in filename order."""

    option_list = []
    for path in sorted((Path.home() / ".ssh").glob("*.pub")):
        label = f"~/.ssh/{path.name}" if show_directory else path.name
        option_list.append(LocalKeyOption(path, label))
    return option_list
