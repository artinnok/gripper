from textual.widgets import Static

from galloc.tui.add_details import format_status


class CatalogStatus(Static):
    """Show each asked provider's catalog status and what GPU addition waits for, with spinners while loading."""

    def __init__(self) -> None:
        """Start with no provider asked and nothing awaited."""

        super().__init__(id="info")
        # None marks a provider whose catalog has not arrived yet.
        self.provider_status_map: dict[str, str | None] = {}
        self.activity: str | None = None

    @property
    def loading(self) -> bool:
        """Report whether a provider's catalog is still awaited."""

        return None in self.provider_status_map.values()

    def start(self, provider_name_list: list[str]) -> None:
        """Mark every asked provider as loading."""

        self.provider_status_map = dict.fromkeys(provider_name_list)
        self._show()

    def set_status(self, provider_name: str, status: str) -> None:
        """Replace one provider's spinner with the outcome of its catalog request."""

        self.provider_status_map[provider_name] = status
        self._show()

    def show_activity(self, activity: str | None) -> None:
        """Name what the screen waits for under a spinner, or remove it."""

        self.activity = activity
        self._show()

    def _show(self) -> None:
        """Render the statuses, animating only while something is awaited."""

        self.auto_refresh = None
        if self.loading or self.activity is not None:
            self.auto_refresh = 0.1
        self.update(format_status(self.provider_status_map, self.activity))
