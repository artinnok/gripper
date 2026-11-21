from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from galloc.core.exceptions import InvalidBudget, InvalidTTL
from galloc.core.pricing import calculate_budget_ttl, calculate_cost, parse_budget
from galloc.core.ttl import format_ttl, parse_ttl
from galloc.providers.schemas import GPUModel, Region
from galloc.tui.wizard import WizardStep, render_progress


class AddStep(StrEnum):
    """Name the steps of choosing a GPU; the last two take the limit of the chosen kind as text."""

    GPU = "gpu"
    SSH_KEY = "ssh_key"
    REGION = "region"
    LIMIT = "limit"
    TTL = "ttl"
    BUDGET = "budget"


BACK_KEYS = "[$foreground]esc[/] back"
CHOICE_KEYS = f"[$foreground]↑↓[/] move   [$foreground]enter[/] select   {BACK_KEYS}"
LIMIT_KEYS = f"[$foreground]enter[/] create   {BACK_KEYS}"

ADD_STEPS = {
    AddStep.GPU: WizardStep(
        "GPU", f"{CHOICE_KEYS}   [$foreground]/[/] search   [$foreground]r[/] refresh", next_step=AddStep.REGION
    ),
    AddStep.SSH_KEY: WizardStep(
        "SSH key", f"[$foreground]r[/] retry   {BACK_KEYS}", next_step=AddStep.REGION, previous_step=AddStep.GPU
    ),
    AddStep.REGION: WizardStep("Region", CHOICE_KEYS, next_step=AddStep.LIMIT, previous_step=AddStep.GPU),
    AddStep.LIMIT: WizardStep(
        "TTL or budget", CHOICE_KEYS, choices=(AddStep.TTL, AddStep.BUDGET), previous_step=AddStep.REGION
    ),
    AddStep.TTL: WizardStep("TTL", LIMIT_KEYS, previous_step=AddStep.LIMIT),
    AddStep.BUDGET: WizardStep("Budget (USD)", LIMIT_KEYS, previous_step=AddStep.LIMIT),
}
LIMIT_PLACEHOLDER_MAP = {AddStep.TTL: "30m, 1h, 1d", AddStep.BUDGET: "2.5"}


def format_progress(step: AddStep, provider_name: str | None, gpu_model: GPUModel | None, region: Region | None) -> str:
    """List the answered steps above the one that is open."""

    answers = {}
    if gpu_model is not None:
        answers[AddStep.GPU] = f"{provider_name} · {gpu_model.name}  ${gpu_model.hourly_price:.2f}/hour"
    if region is not None:
        answers[AddStep.REGION] = region.id
    return render_progress(ADD_STEPS, step, answers)


def parse_limit(step: AddStep, value: str, hourly_price: float) -> timedelta:
    """Validate the entered TTL or budget using the shared CLI pricing and TTL rules."""

    if step == AddStep.TTL:
        return parse_ttl(value)

    budget = parse_budget(value)
    ttl = calculate_budget_ttl(budget, Decimal(str(hourly_price)))
    return parse_ttl(ttl)


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
