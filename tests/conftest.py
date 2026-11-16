import json
from contextlib import ExitStack
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr

from galloc.api_clients.runpod.client import RunpodAPIClient
from galloc.api_clients.vast.client import VastAPIClient


SSH_PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK92FXWTQiQNcZl6wr0NXAR5/GYDsK7fdNzTm5qGLWZd laptop"
SSH_FINGERPRINT = "SHA256:54jwqn6/7U5BRTZ8tEGJcxIqMBYqZZZkao3EcU2ba/Y"
OTHER_SSH_PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP4/RIogqrg1nYtweatRKQdoTYQfpy72GUsEp4iSbYjY desktop"
OTHER_SSH_FINGERPRINT = "SHA256:DSpPr3VkdltX8FFWhqTjXnzQEt7H2mUnqbuv/Iwm3/Q"


@pytest.fixture(autouse=True)
def config_path(tmp_path, monkeypatch):
    """Keep settings away from the user's configuration file."""

    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("galloc.main.CONFIG_PATH", config_path)
    return config_path


@pytest.fixture
def write_config(config_path, tmp_path):
    """Write isolated TOML settings with optional shared and provider overrides."""

    def write(*, defaults=None, runpod=None):
        """Keep test credentials and database paths explicit in the actual config file."""

        section_map = {
            "defaults": {"database_path": str(tmp_path / "state.db")} | (defaults or {}),
            "providers.runpod": {
                "api_key": "test-token",
                "api_url": "https://runpod.test/v2/",
            }
            | (runpod or {}),
        }
        section_list = [
            f"[{section}]\n" + "\n".join(f"{name} = {json.dumps(value)}" for name, value in values.items())
            for section, values in section_map.items()
        ]
        config_path.write_text("\n\n".join(section_list) + "\n")
        return config_path

    return write


def make_gpu_data(**overrides):
    """Build a GPU catalog response with explicit Pod availability and hardware data."""

    data = {
        "id": "NVIDIA H200",
        "name": "H200",
        "secure": True,
        "price": {"secure": 5.29},
        "memory": 141,
        "minRam": 188,
        "minVcpuCount": 12,
        "availability": "HIGH",
        "dataCenters": [
            {"id": region_id, "availability": "HIGH"} for region_id in ["US-KS-2", "EU-NL-1", "CA-MTL-1", "AP-JP-1"]
        ],
    }
    return data | overrides


@pytest.fixture
def make_runpod_client():
    """Create clients with mocked HTTP and close them after each test."""

    with ExitStack() as stack:

        def make_client(handler):
            """Connect one client to the test's explicit HTTP handler."""

            client = RunpodAPIClient(
                SecretStr("test-api-key"),
                base_url="https://runpod.test/v2/",
                timeout=12,
                transport=httpx.MockTransport(handler),
            )
            return stack.enter_context(client)

        yield make_client


@pytest.fixture
def pod_data():
    """Provide a Pod response based on the Runpod v2 OpenAPI example."""

    return {
        "id": "7h9k2m4n6p",
        "name": "pytorch-training",
        "image": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
        "args": "",
        "disk": 50,
        "ports": ["22/tcp"],
        "env": {},
        "registry": None,
        "status": "RUNNING",
        "actions": ["stop", "restart", "terminate"],
        "mounts": {},
        "gpu": {"id": "NVIDIA H200", "count": 1, "vcpuCount": 16, "memory": 64},
        "cloud": "SECURE",
        "dataCenterId": "US-KS-2",
        "cudaVersion": "12.8",
        "ssh": {
            "proxy": {"command": "ssh 7h9k2m4n6p-64411eb2@ssh.runpod.io"},
            "direct": {"command": "ssh root@195.26.233.3 -p 34446"},
        },
        "template": None,
        "cost": 3.99,
        "locked": False,
        "globalNetworking": {"enabled": False},
        "runtime": {"uptime": 5, "gpus": [{"util": 0, "memoryUtil": 0}]},
        "createdAt": "2026-06-01T12:00:00Z",
        "startedAt": "2026-06-01T12:02:00Z",
    }


@pytest.fixture
def vast_offer_data():
    """Supply the fields used from Vast.ai's documented marketplace response."""

    return {
        "id": 1234,
        "gpu_name": "RTX 4090",
        "gpu_ram": 24576,
        "geolocation": "Oregon, US",
        "dph_base": 0.30,
        "dph_total": 0.34,
        "cpu_ram": 65536,
        "cpu_cores_effective": 8,
    }


@pytest.fixture
def vast_instance_data():
    """Supply actual container state and the documented proxy SSH endpoint."""

    return {
        "id": 5678,
        "actual_status": "running",
        "intended_status": "running",
        "geolocation": "Oregon, US",
        "gpu_name": "RTX 4090",
        "dph_total": 0.34,
        "start_date": 1794816000,
        "ssh_host": "ssh1.vast.ai",
        "ssh_port": 12345,
    }


@pytest.fixture
def make_vast_client():
    """Create real Vast.ai clients with isolated mocked HTTP transports."""

    with ExitStack() as stack:

        def make_client(handler):
            """Connect one client to the test's explicit HTTP handler."""

            return stack.enter_context(
                VastAPIClient(
                    SecretStr("vast-token"),
                    base_url="https://vast.test/api/v0/",
                    timeout=12,
                    transport=httpx.MockTransport(handler),
                )
            )

        yield make_client


@pytest.fixture
def vast_http(monkeypatch):
    """Replace only the factory's Vast.ai HTTP transport and reject unarranged calls."""

    handler = Mock(side_effect=AssertionError("Unexpected Vast.ai request."))

    def make_client(api_key, *, base_url, timeout):
        """Keep real authentication, endpoint construction, and response parsing."""

        return VastAPIClient(api_key, base_url=base_url, timeout=timeout, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("galloc.providers.factory.VastAPIClient", make_client)
    return handler
