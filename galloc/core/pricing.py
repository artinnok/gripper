from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from galloc.core.exceptions import InvalidBudget


def parse_budget(value: str) -> Decimal:
    """Parse a positive, finite USD amount without floating-point rounding."""

    try:
        budget = Decimal(value)
    except InvalidOperation as exception:
        raise InvalidBudget("Use a positive budget in USD, such as 5 or 12.50.") from exception

    if not budget.is_finite() or budget <= 0:
        raise InvalidBudget("Use a positive budget in USD, such as 5 or 12.50.")
    return budget


def calculate_budget_ttl(budget: Decimal, hourly_price: Decimal) -> str:
    """Convert a compute budget to whole minutes, rounding the duration down."""

    minutes = (budget * 60 / hourly_price).to_integral_value(rounding=ROUND_DOWN)
    if minutes < 1:
        raise InvalidBudget("Budget is too small for one minute at the selected GPU's catalog rate.")
    if minutes > timedelta.max.days * 24 * 60:
        raise InvalidBudget("Budget produces a TTL that exceeds the supported date range.")
    return f"{minutes:.0f}m"


def calculate_cost(duration: timedelta, hourly_price: Decimal) -> Decimal:
    """Estimate compute cost for a duration at a quoted hourly rate."""

    return hourly_price * Decimal(str(duration.total_seconds())) / 3600
