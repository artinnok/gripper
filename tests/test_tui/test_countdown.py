from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from rich.text import Text
from textual.widgets import DataTable

from gripper.core.schemas import GPU
from gripper.tui.app import GripperApp
from gripper.tui.gpu_details import format_time_left
from tests.test_tui.conftest import run_app


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-1, "expired"),
        (0, "expired"),
        (45, "0m 45s"),
        (599, "9m 59s"),
        (600, "10m"),
        (3599, "59m"),
        (3600, "1h 0m"),
        (8100, "2h 15m"),
        (86399, "23h 59m"),
        (86400, "1d 0h"),
        (273600, "3d 4h"),
    ],
)
def test_countdown_precision_changes_at_time_boundaries(seconds, expected):
    """Use seconds near expiry and progressively coarser units for longer deadlines."""

    now = datetime(2026, 11, 15, tzinfo=UTC)
    gpu = GPU(provider="runpod", expires_at=now + timedelta(seconds=seconds))

    assert Text.from_markup(format_time_left(gpu, now)).plain == expected


def test_countdown_ticks_without_reading_the_provider(settings, config_path, runpod_http, gpu, pod_data):
    """Update the displayed deadline without moving selection or issuing another API request."""

    config_path.touch()
    runpod_http.side_effect = [httpx.Response(200, json=pod_data)]

    async def scenario(pilot):
        """Advance the clock across the ten-minute boundary and wait for the display tick."""

        table = pilot.app.screen.query_one(DataTable)
        cursor = table.cursor_coordinate
        assert table.get_cell(gpu.name, "LEFT") == "10m"

        clock.now.return_value += timedelta(seconds=1)
        await pilot.pause(1.1)

        assert Text.from_markup(table.get_cell(gpu.name, "LEFT")).plain == "9m 59s"
        assert table.cursor_coordinate == cursor

    with patch("gripper.tui.gpu_list.datetime") as clock:
        clock.now.return_value = gpu.expires_at - timedelta(minutes=10)
        run_app(GripperApp(settings, config_path), scenario)

    runpod_http.assert_called_once()
