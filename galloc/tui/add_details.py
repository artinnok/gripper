from rich.console import Group
from rich.spinner import Spinner
from rich.table import Table
from textual.markup import escape
from textual.widgets.option_list import Option

from galloc.providers.schemas import GPUModel, Region
from galloc.tui.theme import AMBER_THEME


LOW_PRICE_THRESHOLD = 0.40
PRICE_COLUMN_WIDTH = 9
MEMORY_COLUMN_WIDTH = 9
STOCK_COLUMN_WIDTH = 9
REGION_COLUMN_WIDTH = 14
READY = f"[{AMBER_THEME.success}]ready[/]"


class GPUOption(Option):
    """Carry the provider and model selected by Enter, even if later results reorder the list."""

    def __init__(self, provider_name: str, gpu_model: GPUModel, name_width: int) -> None:
        """Display the offer under the shared column header."""

        price = f"${gpu_model.hourly_price:.2f}"
        marker = "ϟ" if gpu_model.hourly_price < LOW_PRICE_THRESHOLD else " "
        memory = str(gpu_model.memory_gb) if gpu_model.memory_gb is not None else "-"
        prompt = (
            f"{gpu_model.name:<{name_width}}  {marker}  {price:<{PRICE_COLUMN_WIDTH}}  "
            f"{memory:<{MEMORY_COLUMN_WIDTH}}  "
            f"{gpu_model.availability.title():<{STOCK_COLUMN_WIDTH}}  {provider_name}"
        )
        super().__init__(escape(prompt))
        self.provider_name = provider_name
        self.gpu_model = gpu_model


def format_gpu_model_list(gpu_model_list: list[tuple[str, GPUModel]]) -> tuple[str, list[GPUOption]]:
    """Align every provider's GPU models under one column header, returned beside the rows."""

    name_width = max([len("GPU"), *(len(gpu_model.name) for provider_name, gpu_model in gpu_model_list)])
    columns = (
        f"{'GPU':<{name_width}}     {'USD/HOUR':<{PRICE_COLUMN_WIDTH}}  "
        f"{'VRAM (GB)':<{MEMORY_COLUMN_WIDTH}}  {'STOCK':<{STOCK_COLUMN_WIDTH}}  PROVIDER"
    )
    row_list = [GPUOption(provider_name, gpu_model, name_width) for provider_name, gpu_model in gpu_model_list]
    return columns, row_list


def format_region_list(region_list: list[Region]) -> tuple[str, list[str]]:
    """Align region IDs and locations under their column header, returned beside the rows."""

    columns = f"{'ID':<{REGION_COLUMN_WIDTH}}  LOCATION"
    return columns, [escape(f"{region.id:<{REGION_COLUMN_WIDTH}}  {region.location}") for region in region_list]


def format_status(provider_status_map: dict[str, str | None], activity: str | None) -> Table | Group:
    """Name every asked provider beside its status, and below them what the screen waits for, with spinners."""

    status_grid = Table.grid(padding=(0, 2))
    for provider_name, status in provider_status_map.items():
        status_grid.add_row(provider_name, status or Spinner("arc", "loading"))
    if activity is None:
        return status_grid
    return Group(status_grid, "", Spinner("arc", activity))
