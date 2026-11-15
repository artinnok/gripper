from textual.markup import escape
from textual.widgets import Static


class Message(Static):
    """Show the outcome of the last action inline until the next key press."""

    def show(self, text: str, *, error: bool = False) -> None:
        """Replace the previous message, marking a failure with the error color."""

        color = "$error" if error else "$success"
        self.update(f"[{color}]{escape(text)}[/]")
