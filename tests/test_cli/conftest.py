from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import Mock

import httpx
import pytest
from click.testing import CliRunner
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input

from galloc.api_clients.runpod.client import RunpodAPIClient
from galloc.core import storage
from galloc.core.schemas import GPU
from tests.conftest import SSH_PUBLIC_KEY


class TerminalInput(BytesIO):
    """Supply terminal input to Click without mocking application behavior."""

    def isatty(self) -> bool:
        """Report an interactive terminal for the command's input check."""

        return True


@pytest.fixture
def keyboard():
    """Send key presses through the real selector's input handling."""

    with create_pipe_input() as pipe_input, create_app_session(input=pipe_input):
        yield pipe_input


@pytest.fixture(autouse=True)
def database_path(tmp_path, monkeypatch, write_config):
    """Load CLI settings and persisted state from a temporary TOML configuration."""

    monkeypatch.chdir(tmp_path)
    write_config()
    return tmp_path / "state.db"


@pytest.fixture
def runner():
    """Invoke the installed CLI's Click entry point."""

    return CliRunner()


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
def gpu(database_path, pod_data):
    """Save a running GPU for commands that operate on an existing instance."""

    now = datetime.now(UTC)
    gpu = GPU(
        provider="runpod",
        provider_instance_id=pod_data["id"],
        expires_at=now + timedelta(minutes=30),
    )
    with storage.open_database(database_path) as connection:
        storage.create_gpu(connection, gpu)
    return gpu


CREATION_TIME = datetime(2026, 11, 13, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def creation_clock(monkeypatch):
    """Freeze preparation time so TTL checks do not require a persisted creation timestamp."""

    class CreationClock(datetime):
        """Provide a deterministic clock only at the creation boundary."""

        @classmethod
        def now(cls, tz=None):
            """Return the fixed preparation timestamp in the requested timezone."""

            return CREATION_TIME.astimezone(tz)

    monkeypatch.setattr("galloc.core.validators.datetime", CreationClock)


@pytest.fixture
def public_key_path(tmp_path):
    """Provide a disposable public key file without reading the user's SSH directory."""

    path = tmp_path / "id_ed25519.pub"
    path.write_text(SSH_PUBLIC_KEY + "\n")
    return path
