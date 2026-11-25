import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from textual.widgets import DataTable, Input, OptionList

from gripper.core import storage
from gripper.core.schemas import GPU
from gripper.settings import Settings, load_settings
from gripper.tui.add import AddScreen
from gripper.tui.app import GripperApp
from gripper.tui.gpu_list import GPUListScreen
from gripper.tui.message import Message
from tests.conftest import SSH_PUBLIC_KEY
from tests.test_tui.conftest import run_app, settle


def test_vast_setup_uses_its_credentials_and_shared_defaults(settings, config_path, home, vast_http, runpod_http):
    """Select the second provider, register a public key, and save a usable Vast-only config."""

    public_key_path = home / ".ssh" / "id_ed25519.pub"
    public_key_path.parent.mkdir()
    public_key_path.write_text(SSH_PUBLIC_KEY)
    vast_http.side_effect = [
        httpx.Response(200, json=[]),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"success": True}),
    ]

    async def scenario(pilot):
        """Complete the normal setup flow with Vast.ai selected instead of Runpod."""

        await pilot.press("down", "enter")
        pilot.app.screen.query_one(Input).value = "entered-vast-key"
        await pilot.press("enter")
        await settle(pilot)
        pilot.app.screen.query_one(Input).value = "80"
        await pilot.press("enter", "enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)
        assert pilot.app.settings.provider == "vast"

    run_app(GripperApp(settings, config_path), scenario)
    saved = load_settings(config_path)
    assert saved.provider == "vast"
    assert saved.disk_gb == saved.vast_disk_gb == 80
    assert saved.runpod_api_key is None
    assert all(call.args[0].headers["Authorization"] == "Bearer entered-vast-key" for call in vast_http.call_args_list)
    assert json.loads(vast_http.call_args.args[0].content) == {"ssh_key": SSH_PUBLIC_KEY}
    runpod_http.assert_not_called()


@pytest.mark.parametrize("status", [401, 404])
def test_vast_setup_rejects_invalid_credentials(settings, config_path, vast_http, status):
    """Reject an invalid key through an authenticated endpoint even when the catalog is public."""

    def handler(request):
        """Allow public marketplace reads but reject access to account keys."""

        if request.url.path.endswith("bundles/"):
            return httpx.Response(200, json={"offers": []})
        assert request.url.path.endswith("ssh/")
        return httpx.Response(status, json={"success": False, "error": "auth_error", "msg": "Invalid user key"})

    vast_http.side_effect = handler

    async def scenario(pilot):
        """Remain on the credential field after the provider refuses it."""

        await pilot.press("down", "enter")
        pilot.app.screen.query_one(Input).value = "invalid-vast-key"
        await pilot.press("enter")
        await settle(pilot)
        assert pilot.app.screen.query_one(Input).password
        assert not pilot.app.screen.query_one(Input).disabled
        assert not config_path.exists()

    run_app(GripperApp(settings, config_path), scenario)


@pytest.mark.parametrize("budget", [False, True])
def test_vast_add_and_deadline_on_another_screen(
    tmp_path, config_path, vast_http, vast_offer_data, vast_instance_data, monkeypatch, budget, local_key
):
    """Select an exact offer, wait for readiness, then expire it while the SSH-key screen is open."""

    config_path.touch()
    settings = Settings(provider="vast", vast_api_key=SecretStr("vast-token"), database_path=tmp_path / "state.db")
    monkeypatch.setattr("gripper.tui.app.TTL_CHECK_INTERVAL", 0.05)
    deleted = False

    def handler(request):
        """Serve only the Vast.ai endpoints needed by the real TUI workflow."""

        nonlocal deleted
        if request.url.path.endswith("bundles/"):
            return httpx.Response(200, json={"offers": [vast_offer_data]})
        if request.url.path.endswith("ssh/"):
            return httpx.Response(200, json=[{"id": 1, "key": local_key.public_key}])
        if request.method == "PUT":
            assert request.url.path.endswith("asks/1234/")
            return httpx.Response(200, json={"success": True, "new_contract": 5678})
        if request.method == "DELETE":
            deleted = True
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"instances": None if deleted else vast_instance_data})

    vast_http.side_effect = handler

    async def scenario(pilot):
        """Drive the shared screen and verify that TTL work outlives navigation."""

        await pilot.press("a")
        await settle(pilot)
        row = pilot.app.screen.query_one("#choice", OptionList).get_option_at_index(0).prompt
        assert "RTX 4090" in row and "#" not in row and "ϟ" in row and "Available" in row
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter")
        if budget:
            await pilot.press("down")
        await pilot.press("enter")
        pilot.app.screen.query_one("#limit", Input).value = "2.5" if budget else "30m"
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, GPUListScreen)
        assert pilot.app.screen.query_one(DataTable).row_count == 1
        with storage.open_database(settings.database_path) as connection:
            (gpu,) = storage.get_gpu_list(connection)
            expected = timedelta(minutes=500 if budget else 30)
            assert expected - timedelta(seconds=10) < gpu.expires_at - datetime.now(UTC) <= expected
        await pilot.press("k")
        await settle(pilot)
        with storage.open_database(settings.database_path) as connection:
            gpu.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            storage.update_gpu_expiration(connection, gpu)
        await pilot.pause(0.3)
        await settle(pilot)
        assert deleted
        with storage.open_database(settings.database_path) as connection:
            assert storage.get_gpu_list(connection) == []

    run_app(GripperApp(settings, config_path), scenario)


def test_failure_before_the_rental_request_returns_to_gpu_selection(
    tmp_path, config_path, vast_http, vast_offer_data, local_key
):
    """Choose again with the reason shown when the start fails before anything is rented."""

    config_path.touch()
    settings = Settings(provider="vast", vast_api_key=SecretStr("vast-token"), database_path=tmp_path / "state.db")
    lookup_count = 0

    def handler(request):
        """Serve the marketplace, then an unreadable answer to the final check of the chosen offer."""

        nonlocal lookup_count
        if request.url.path.endswith("ssh/"):
            return httpx.Response(200, json=[{"id": 1, "key": local_key.public_key}])
        assert (request.method, request.url.path) == ("POST", "/api/v0/bundles/")
        if "ask_contract_id" not in json.loads(request.content):
            return httpx.Response(200, json={"offers": [vast_offer_data]})

        lookup_count += 1
        if lookup_count == 2:
            return httpx.Response(200, json={"offers": [{"id": 1234}]})
        return httpx.Response(200, json={"offers": [vast_offer_data]})

    vast_http.side_effect = handler

    async def scenario(pilot):
        """Submit a complete selection whose final validation cannot be read."""

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("enter", "enter", "1", "h", "enter")
        await settle(pilot)

        screen = pilot.app.screen
        assert isinstance(screen, AddScreen)
        assert screen.step == "gpu"
        assert screen.query_one("#choice", OptionList).option_count == 1
        assert "invalid response to POST /api/v0/bundles/ (offers.0.gpu_name" in screen.query_one(Message).content

    run_app(GripperApp(settings, config_path), scenario)
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == []


def test_startup_expires_both_providers(
    settings, config_path, expired_gpu, pod_data, runpod_http, vast_http, vast_instance_data
):
    """Use each saved GPU's provider for overdue deletion regardless of the selected default."""

    config_path.touch()
    settings.vast_api_key = SecretStr("vast-token")
    vast_gpu = GPU(provider="vast", provider_instance_id="5678", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    with storage.open_database(settings.database_path) as connection:
        storage.create_gpu(connection, vast_gpu)

    def runpod_handler(request):
        """Serve Runpod reads and its specific deletion acknowledgement."""

        return httpx.Response(204) if request.method == "DELETE" else httpx.Response(200, json=pod_data)

    def vast_handler(request):
        """Serve Vast.ai reads and its different deletion acknowledgement."""

        payload = {"success": True} if request.method == "DELETE" else {"instances": vast_instance_data}
        return httpx.Response(200, json=payload)

    runpod_http.side_effect = runpod_handler
    vast_http.side_effect = vast_handler

    async def scenario(pilot):
        """Wait for startup cleanup and its table refresh."""

        await settle(pilot)
        with storage.open_database(settings.database_path) as connection:
            assert storage.get_gpu_list(connection) == []
        assert pilot.app.screen.query_one(DataTable).row_count == 0

    run_app(GripperApp(settings, config_path), scenario)
    for handler in (runpod_http, vast_http):
        assert sum(call.args[0].method == "DELETE" for call in handler.call_args_list) == 1
