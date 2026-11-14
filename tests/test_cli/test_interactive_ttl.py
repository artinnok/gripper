import json
from datetime import timedelta

import httpx
import pytest
import questionary

from galloc.core import storage
from galloc.main import cli
from tests.conftest import make_gpu_data
from tests.test_cli.conftest import CREATION_TIME, TerminalInput


@pytest.fixture
def creation_response_list(pod_data):
    """Supply catalog validation and creation responses after TTL selection."""

    return [
        httpx.Response(
            200, json={"gpus": [make_gpu_data(id="NVIDIA H200", name="H200 SXM", secure=True, price={"secure": 5.29})]}
        ),
        httpx.Response(200, json={"dataCenters": [{"id": "US-KS-2", "name": "US-KS-2", "region": "NORTH_AMERICA"}]}),
        httpx.Response(201, json=pod_data),
    ]


@pytest.mark.parametrize("steps, minutes", [(0, 30), (1, 60), (2, 180), (3, 360)])
def test_ttl_presets(runner, runpod_http, database_path, keyboard, creation_response_list, steps, minutes):
    """Save the selected duration, defaulting to 30 minutes on Enter, with clean JSON output."""

    runpod_http.side_effect = creation_response_list
    keyboard.send_bytes(b"\x1b[B" * steps + b"\r")

    result = runner.invoke(
        cli, ["add", "--gpu", "H200 SXM", "--region", "US-KS-2", "--output", "json"], input=TerminalInput()
    )

    assert result.exit_code == 0, result.output
    with storage.open_database(database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.expires_at - CREATION_TIME == timedelta(minutes=minutes)
    assert json.loads(result.stdout)["id"] == str(gpu.id)
    assert "TTL format:" in result.stderr
    assert "30m, 1h, 1d" in result.stderr
    assert "● 30 minutes (30m)" in result.stderr
    assert "○ Custom duration" in result.stderr
    assert "»" not in result.stderr


@pytest.mark.parametrize(
    "value, duration", [("45m", timedelta(minutes=45)), ("2h", timedelta(hours=2)), ("1d", timedelta(days=1))]
)
def test_custom_ttl(runner, runpod_http, database_path, keyboard, creation_response_list, value, duration):
    """Accept custom durations in each supported unit through the actual input prompt."""

    runpod_http.side_effect = creation_response_list
    keyboard.send_bytes(b"\x1b[B" * 4 + b"\r" + value.encode() + b"\r")

    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM", "--region", "US-KS-2"], input=TerminalInput())

    assert result.exit_code == 0, result.output
    assert "Enter TTL:" in result.stderr
    with storage.open_database(database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.expires_at - CREATION_TIME == duration


def test_invalid_custom_ttl_can_be_corrected(runner, runpod_http, database_path, keyboard, creation_response_list):
    """Reject invalid input within the prompt, then create once with the corrected duration."""

    runpod_http.side_effect = creation_response_list
    keyboard.send_bytes(b"\x1b[B" * 4 + b"\r0m\r\x7f\x7f45m\r")

    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM", "--region", "US-KS-2"], input=TerminalInput())

    assert result.exit_code == 0, result.output
    with storage.open_database(database_path) as connection:
        (gpu,) = storage.get_gpu_list(connection)
    assert gpu.expires_at - CREATION_TIME == timedelta(minutes=45)
    assert runpod_http.call_count == 3


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("cancel_key", [b"\x1b", b"\x03"])
def test_cancel_ttl(runner, runpod_http, database_path, keyboard, monkeypatch, custom, cancel_key):
    """Cancel either TTL prompt without HTTP requests or saved creation intent."""

    text_prompt = questionary.text

    def make_text_prompt(*args, **kwargs):
        """Send cancellation when custom input opens, after the selector has exited."""

        question = text_prompt(*args, **kwargs)
        question.application.pre_run_callables.append(lambda: keyboard.send_bytes(cancel_key))
        return question

    monkeypatch.setattr(questionary, "text", make_text_prompt)
    if custom:
        keyboard.send_bytes(b"\x1b[B" * 4 + b"\r")
    else:
        keyboard.send_bytes(cancel_key)

    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM", "--region", "US-KS-2"], input=TerminalInput())

    assert result.exit_code == 1, result.output
    assert "Aborted!" in result.stderr
    assert result.stdout.strip() == ""
    runpod_http.assert_not_called()
    with storage.open_database(database_path) as connection:
        assert storage.get_gpu_list(connection) == []


def test_region_then_ttl(runner, runpod_http, keyboard, creation_response_list):
    """Complete both selectors in order when neither option is supplied."""

    gpu_response, region_response, _ = creation_response_list
    runpod_http.side_effect = [gpu_response, region_response, *creation_response_list]
    keyboard.send_bytes(b"\r\x1b[B\r")

    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM"], input=TerminalInput())

    assert result.exit_code == 0, result.output
    assert result.stderr.index("Select a region") < result.stderr.index("Select a TTL")
    assert runpod_http.call_count == 5


def test_missing_ttl_without_terminal(runner, runpod_http, database_path):
    """Require an explicit TTL in scripts before opening storage or making requests."""

    result = runner.invoke(cli, ["add", "--gpu", "H200 SXM", "--region", "US-KS-2"], input="30m\n")

    assert result.exit_code == 2
    assert "Pass --ttl" in result.stderr
    assert result.stdout == ""
    runpod_http.assert_not_called()
    assert not database_path.exists()
