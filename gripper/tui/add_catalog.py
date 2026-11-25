from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, OptionList, Static

from gripper.providers.schemas import GPUModel
from gripper.tui.add_details import GPUOption, format_gpu_model_list


class GPUCatalog(Vertical):
    """List every provider's GPU models, most expensive first, under a search field that filters them as typed."""

    BINDINGS = [
        Binding("/", "search", "Search"),
        Binding("up", "leave_search(True)", "Previous result", show=False),
        Binding("down", "leave_search(False)", "Next result", show=False),
    ]

    def __init__(self) -> None:
        """Start with no models and the search field closed."""

        super().__init__()
        self.gpu_model_list: list[tuple[str, GPUModel]] = []

    def compose(self) -> ComposeResult:
        """Place the search field above the column header, the rows, and the empty-search note."""

        search = Input(id="search", placeholder="Search GPU or provider", compact=True, select_on_focus=False)
        search.display = False
        yield search
        yield Static(id="columns")
        yield OptionList(id="choice", compact=True)
        yield Static(id="note")

    @property
    def searching(self) -> bool:
        """Report whether the search field is open."""

        return self.query_one(Input).display

    def clear(self) -> None:
        """Forget the models of the previous catalog, keeping the search."""

        self.gpu_model_list = []
        self._show_rows(None)

    def add_gpu_model_list(self, provider_name: str, gpu_model_list: list[GPUModel]) -> None:
        """Merge one provider's models in price order without moving a cursor the user placed."""

        # Keep a chosen GPU under the cursor while its SSH key is checked, even if it was the first row.
        highlighted = None
        cursor_index = self.query_one(OptionList).highlighted
        if self.disabled or (cursor_index is not None and cursor_index > 0):
            highlighted = self._get_highlighted_gpu_model()
        self.gpu_model_list = sorted(
            self.gpu_model_list + [(provider_name, gpu_model) for gpu_model in gpu_model_list],
            key=lambda row: row[1].hourly_price,
            reverse=True,
        )
        self._show_rows(highlighted)

    def show(self, highlighted: tuple[str, GPUModel] | None) -> None:
        """Put the given model under the cursor and the keys in the rows."""

        self._show_rows(highlighted)
        self.query_one(OptionList).focus()

    def close_search(self) -> None:
        """Clear and hide the search field, keeping the found model under the cursor in the whole catalog."""

        highlighted = self._get_highlighted_gpu_model()
        search = self.query_one(Input)
        with self.prevent(Input.Changed):
            search.clear()
        search.display = False
        self.show(highlighted)

    def action_search(self) -> None:
        """Open the search field, or return to it with its query."""

        search = self.query_one(Input)
        search.display = True
        search.focus()

    def action_leave_search(self, previous: bool) -> None:
        """Move from the search field into the rows in the direction of the pressed arrow."""

        choice = self.query_one(OptionList)
        choice.focus()
        if previous:
            choice.action_cursor_up()
        else:
            choice.action_cursor_down()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the rows as the query changes, keeping the model under the cursor while it matches."""

        event.stop()
        self._show_rows(self._get_highlighted_gpu_model())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Move from the search field to the rows it left."""

        event.stop()
        self.query_one(OptionList).focus()

    def _show_rows(self, highlighted: tuple[str, GPUModel] | None) -> None:
        """List the models whose GPU name and provider contain every word of the query."""

        term_list = self.query_one(Input).value.casefold().split()
        visible_gpu_model_list = [
            (provider_name, gpu_model)
            for provider_name, gpu_model in self.gpu_model_list
            if all(term in f"{gpu_model.name} {provider_name}".casefold() for term in term_list)
        ]
        columns, row_list = format_gpu_model_list(visible_gpu_model_list)
        self.query_one("#columns", Static).update(columns)
        note = ""
        if term_list and not row_list:
            note = "No matching GPUs."
        self.query_one("#note", Static).update(note)

        index = 0
        if highlighted in visible_gpu_model_list:
            index = visible_gpu_model_list.index(highlighted)

        choice = self.query_one(OptionList)
        choice.set_options(row_list)
        choice.highlighted = index

    def _get_highlighted_gpu_model(self) -> tuple[str, GPUModel] | None:
        """Return the provider and model under the cursor, or None while no row is listed."""

        option = self.query_one(OptionList).highlighted_option
        if isinstance(option, GPUOption):
            return option.provider_name, option.gpu_model
        return None
