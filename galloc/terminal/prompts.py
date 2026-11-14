import sys

import click
import questionary
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent, merge_key_bindings
from prompt_toolkit.output import create_output
from questionary.prompts.common import InquirerControl
from questionary.question import Question

from galloc.core.exceptions import InvalidTTL
from galloc.core.ttl import parse_ttl
from galloc.providers.base import Provider
from galloc.providers.exceptions import GPUUnavailable


LOW_PRICE_THRESHOLD = 0.40
SELECTION_HINT = "(↑/↓ to move, Enter to select, Esc to cancel)"


def prompt_creation_options(
    provider: Provider,
    provider_name: str,
    *,
    gpu: str | None,
    region: str | None,
    ttl: str | None,
    budget: str | None,
) -> tuple[str, str, str | None]:
    """Fill missing creation options in order, preserving explicitly supplied values."""

    if gpu is None:
        gpu = _select_gpu(provider, provider_name, region)
    if region is None:
        region = _select_region(provider, provider_name, gpu)
    if ttl is None and budget is None:
        ttl = _select_ttl()
    return gpu, region, ttl


def _select_gpu(provider: Provider, provider_name: str, region: str | None) -> str:
    """Select an available GPU, limited to the region when one is supplied."""

    location = f" in {region}" if region is not None else ""
    gpu_list = provider.get_gpu_list(region)
    if not gpu_list:
        raise click.ClickException(f"No GPUs currently available for {provider_name}{location}.")

    name_width = max(len(gpu.name) for gpu in gpu_list)
    choice_list = [
        questionary.Choice(
            f"{gpu.name:<{name_width}}  {'ϟ' if gpu.hourly_price < LOW_PRICE_THRESHOLD else '':2}  "
            f"${gpu.hourly_price:<8.2f}  {gpu.memory_gb:<9}  "
            f"{gpu.min_ram_gb if gpu.min_ram_gb is not None else '-':<10}  "
            f"{gpu.min_vcpu_count if gpu.min_vcpu_count is not None else '-':<8}  {gpu.availability.title()}",
            value=gpu.id,
        )
        for gpu in gpu_list
    ]
    return _select_choice(
        f"Select a GPU for {provider_name}{location}:",
        choice_list,
        instruction=(
            f"{SELECTION_HINT}\n"
            "Availability may change before creation. ϟ Under $0.40/hour.\n"
            f"   {'GPU':<{name_width + 6}}  "
            f"{'USD/HOUR':<9}  {'VRAM (GB)':<9}  {'RAM GB MIN':<10}  {'vCPU MIN':<8}  STOCK"
        ),
    )


def _select_region(provider: Provider, provider_name: str, gpu: str) -> str:
    """Select a region with current capacity for the chosen GPU."""

    region_list = provider.get_region_list(gpu)
    if not region_list:
        raise GPUUnavailable(f"No regions currently available for {gpu} on {provider_name}.")

    choice_list = [questionary.Choice(f"{region.id:<14}  {region.location}", value=region.id) for region in region_list]

    return _select_choice(
        f"Select a region for {provider_name}:",
        choice_list,
        instruction=f"{SELECTION_HINT}\n   {'ID':<16}  LOCATION",
    )


def _select_ttl() -> str:
    """Choose a preset duration or enter a validated custom TTL."""

    click.echo("TTL format: 30m, 1h, 1d (minutes, hours, days).", err=True)
    ttl = _select_choice(
        "Select a TTL:",
        [
            questionary.Choice("30 minutes (30m)", value="30m"),
            questionary.Choice("1 hour (1h)", value="1h"),
            questionary.Choice("3 hours (3h)", value="3h"),
            questionary.Choice("6 hours (6h)", value="6h"),
            questionary.Choice("Custom duration", value="custom"),
        ],
    )
    if ttl != "custom":
        return ttl

    return _ask(
        questionary.text(
            "Enter TTL:",
            instruction="(30m, 1h, 1d; Esc to cancel)",
            validate=_validate_ttl,
            output=create_output(stdout=sys.stderr),
        )
    )


def _validate_ttl(value: str) -> bool | str:
    """Keep invalid custom input in the prompt and show the existing TTL error."""

    try:
        parse_ttl(value)
    except InvalidTTL as exception:
        return str(exception)
    return True


def _select_choice(message: str, choice_list: list[questionary.Choice], *, instruction: str = SELECTION_HINT) -> str:
    """Select a value with the shared arrow-key controls and circle indicators."""

    question = questionary.select(
        message,
        choices=choice_list,
        pointer=None,
        use_indicator=True,
        instruction=instruction,
        output=create_output(stdout=sys.stderr),
    )
    question.application.before_render += _update_selection_indicator
    return _ask(question)


def _update_selection_indicator(application: Application) -> None:
    """Fill only the indicator of the currently highlighted choice."""

    (control,) = (control for control in application.layout.find_all_controls() if isinstance(control, InquirerControl))
    control.selected_options = [control.get_pointed_at().value]


def _ask(question: Question) -> str:
    """Ask on stderr and cancel on Esc through the same path as Ctrl+C."""

    key_bindings = KeyBindings()

    @key_bindings.add("escape")
    def cancel_selection(event: KeyPressEvent) -> None:
        """Abort the current prompt before creation."""

        event.app.exit(exception=KeyboardInterrupt)

    question.application.key_bindings = merge_key_bindings([question.application.key_bindings, key_bindings])
    return question.unsafe_ask()
