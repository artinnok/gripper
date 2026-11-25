from unittest.mock import Mock

import httpx
import pytest
from textual.widgets import Input, Link

from gripper.tui.app import GripperApp
from tests.test_tui.conftest import run_app, settle


@pytest.mark.parametrize("first_run", [True, False])
@pytest.mark.parametrize(
    "row, url",
    [
        (0, "https://console.runpod.io/user/credentials"),
        (1, "https://cloud.vast.ai/manage-keys/"),
        (2, "https://cloud.lambda.com/user/api-keys"),
    ],
)
def test_provider_key_link_opens_only_on_request(settings, config_path, first_run, row, url):
    """Both onboarding paths open only the selected provider and preserve the masked input."""

    if not first_run:
        config_path.touch()
    app = GripperApp(settings, config_path)
    app.open_url = Mock()

    async def scenario(pilot):
        """Open the selected location by keyboard and by mouse, then continue typing the key."""

        await pilot.resize_terminal(80, 24)
        if not first_run:
            await pilot.press("p")
        for _ in range(row):
            await pilot.press("down")
        await pilot.press("enter" if first_run else "a")
        screen = app.screen
        prompt = screen.query_one(Input)
        prompt.value = "partial-key"
        link = screen.query_one(Link)
        assert link.url == url
        assert screen.region.contains_region(link.region) and screen.region.contains_region(prompt.region)
        app.open_url.assert_not_called()
        await pilot.press("f2")
        app.open_url.assert_called_once_with(url)
        await pilot.click(Link)
        assert app.open_url.call_count == 2 and app.open_url.call_args.args == (url,)
        assert prompt.password and prompt.has_focus and prompt.value == "partial-key"

    run_app(app, scenario)


def test_lambda_key_onboarding(settings, config_path, lambda_http, local_key):
    """Validate credentials, choose the included disk minimum and register a local identity."""

    lambda_http.side_effect = [
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"data": {"id": "id1", "name": "key1", "public_key": local_key.public_key}}),
    ]
    app = GripperApp(settings, config_path)

    async def scenario(pilot):
        """Complete the Lambda first-run flow through the real configuration file."""

        await pilot.press("down", "down", "enter")
        app.screen.query_one(Input).value = "lambda-test-token"
        await pilot.press("enter")
        await settle(pilot)
        assert not app.screen.query_one(Link).display
        await pilot.press("enter", "enter")
        await settle(pilot)
        assert app.settings.provider == "lambda"

    run_app(app, scenario)
    assert "[providers.lambda]" in config_path.read_text()
    assert str(app.settings.lambda_ssh_public_key) in config_path.read_text()
    assert app.settings.lambda_ssh_public_key.name == "id_ed25519.pub"
    assert lambda_http.call_count == 3
