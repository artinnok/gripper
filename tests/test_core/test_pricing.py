from datetime import timedelta
from decimal import Decimal

import pytest

from gripper.core.exceptions import InvalidBudget
from gripper.core.pricing import calculate_budget_ttl, calculate_cost, parse_budget
from gripper.core.ttl import parse_ttl


@pytest.mark.parametrize("value", ["5", "12.50", "0.01"])
def test_parse_budget(value):
    """Preserve decimal USD amounts without conversion through float."""

    assert parse_budget(value) == Decimal(value)


@pytest.mark.parametrize("value", ["", "invalid", "0", "-1", "NaN", "sNaN", "Infinity", "-Infinity"])
def test_invalid_budget(value):
    """Reject nonpositive and nonfinite amounts before any arithmetic."""

    with pytest.raises(InvalidBudget, match="positive budget"):
        parse_budget(value)


@pytest.mark.parametrize(
    ("budget", "price", "ttl"),
    [("10", "3", "200m"), ("1", "3", "20m"), ("10", "5.29", "113m"), ("0.01", "0.60", "1m")],
)
def test_budget_rounds_down_without_exceeding_estimated_cost(budget, price, ttl):
    """Use the longest whole-minute duration within the compute budget."""

    budget = Decimal(budget)
    price = Decimal(price)
    assert calculate_budget_ttl(budget, price) == ttl
    duration = parse_ttl(ttl)
    assert calculate_cost(duration, price) <= budget
    assert calculate_cost(duration + timedelta(minutes=1), price) > budget


def test_budget_too_small():
    """Reject a budget that cannot cover one minute."""

    with pytest.raises(InvalidBudget, match="too small"):
        calculate_budget_ttl(Decimal("0.01"), Decimal("5.29"))


def test_budget_too_large():
    """Report unusable durations as an input error."""

    with pytest.raises(InvalidBudget, match="date range"):
        calculate_budget_ttl(Decimal("1e20"), Decimal("3"))


def test_ttl_cost():
    """Calculate the reverse estimate from the duration and catalog price."""

    assert calculate_cost(timedelta(minutes=30), Decimal("5.29")) == Decimal("2.645")
