from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from textual.markup import escape

from galloc.core.exceptions import InvalidBudget, InvalidTTL
from galloc.core.pricing import calculate_budget_ttl, calculate_cost, parse_budget
from galloc.core.ttl import format_ttl, parse_ttl
from galloc.providers.schemas import GPUModel, Region
from galloc.tui.theme import CURRENT, DONE


class AddStep(StrEnum):
    """Name the steps of choosing a GPU; the last two take the limit of the chosen kind as text."""

    GPU = "gpu"
    SSH_KEY = "ssh_key"
    REGION = "region"
    LIMIT = "limit"
    TTL = "ttl"
    BUDGET = "budget"


CHOICE_KEYS = "[$foreground]↑↓[/] move   [$foreground]enter[/] select   [$foreground]esc[/] back"
LIMIT_KEYS = "[$foreground]enter[/] create   [$foreground]esc[/] back"
KEYS_MAP = {
    AddStep.GPU: f"{CHOICE_KEYS}   [$foreground]/[/] search   [$foreground]r[/] refresh",
    AddStep.SSH_KEY: "[$foreground]r[/] retry   [$foreground]esc[/] back",
    AddStep.REGION: CHOICE_KEYS,
    AddStep.LIMIT: CHOICE_KEYS,
    AddStep.TTL: LIMIT_KEYS,
    AddStep.BUDGET: LIMIT_KEYS,
}
LIMIT_LABEL_MAP = {AddStep.TTL: "TTL", AddStep.BUDGET: "Budget (USD)"}
LIMIT_PLACEHOLDER_MAP = {AddStep.TTL: "30m, 1h, 1d", AddStep.BUDGET: "2.5"}


def format_progress(step: AddStep, provider_name: str | None, gpu_model: GPUModel | None, region: Region | None) -> str:
    """List the answered steps above the one that is open."""

    if step == AddStep.GPU:
        return f"{CURRENT} GPU"

    gpu_line = f"{DONE} GPU      {provider_name} · {escape(gpu_model.name)}  ${gpu_model.hourly_price:.2f}/hour"
    if step == AddStep.SSH_KEY:
        return f"{gpu_line}\n{CURRENT} SSH key"
    if step == AddStep.REGION:
        return f"{gpu_line}\n{CURRENT} Region"

    label = LIMIT_LABEL_MAP.get(step, "TTL or budget")
    return f"{gpu_line}\n{DONE} Region   {escape(region.id)}\n{CURRENT} {label}"


def parse_limit(step: AddStep, value: str, hourly_price: float) -> timedelta:
    """Validate the entered TTL or budget using the shared CLI pricing and TTL rules."""

    if step == AddStep.TTL:
        return parse_ttl(value)
    return parse_ttl(calculate_budget_ttl(parse_budget(value), Decimal(str(hourly_price))))


def format_limit_preview(step: AddStep, value: str, hourly_price: float) -> str:
    """Preview the cost of a TTL or the duration a budget buys; an unfinished entry previews nothing."""

    try:
        duration = parse_limit(step, value, hourly_price)
    except InvalidTTL, InvalidBudget:
        return ""

    if step == AddStep.BUDGET:
        return f"Estimated TTL: {format_ttl(duration)} · not a spending cap"
    cost = calculate_cost(duration, Decimal(str(hourly_price)))
    return f"about ${cost:.2f} · compute only"
