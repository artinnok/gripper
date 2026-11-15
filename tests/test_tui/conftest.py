import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr
from textual.pilot import Pilot

from galloc.api_clients.runpod.client import RunpodAPIClient
from galloc.core import storage
from galloc.core.schemas import GPU
from galloc.settings import Settings
from galloc.tui.app import GallocApp


def run_app(app: GallocApp, scenario) -> None:
    """Drive the real application headlessly through one test scenario."""

    async def run_scenario():
        """Keep the application open only while the scenario runs."""

        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot)
            await scenario(pilot)

    asyncio.run(run_scenario())


async def settle(pilot: Pilot) -> None:
    """Wait for provider work started by the last key press and for its screen update."""

    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Keep SSH key discovery and generation away from the user's home directory."""

    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def settings(tmp_path):
    """Provide explicit settings that ignore the environment and any .env file."""

    return Settings(
        _env_file=None,
        database_path=tmp_path / "state.db",
        runpod_api_key=SecretStr("test-token"),
        runpod_api_url="https://runpod.test/v2/",
    )


@pytest.fixture(autouse=True)
def runpod_http(monkeypatch):
    """Replace only HTTP transport and reject requests a test did not arrange."""

    handler = Mock(side_effect=AssertionError("Unexpected HTTP request."))

    def make_client(api_key, *, base_url, timeout):
        """Use the real client with the test's explicit HTTP responses."""

        return RunpodAPIClient(api_key, base_url=base_url, timeout=timeout, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("galloc.providers.factory.RunpodAPIClient", make_client)
    return handler


@pytest.fixture
def gpu(settings, pod_data):
    """Save a running GPU for screens that operate on an existing instance."""

    gpu = GPU(
        provider="runpod",
        provider_instance_id=pod_data["id"],
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    with storage.open_database(settings.database_path) as connection:
        storage.create_gpu(connection, gpu)
    return gpu
