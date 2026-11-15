from contextlib import ExitStack

import httpx
import pytest
from pydantic import SecretStr

from galloc.api_clients.runpod.client import RunpodAPIClient


SSH_PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK92FXWTQiQNcZl6wr0NXAR5/GYDsK7fdNzTm5qGLWZd laptop"
SSH_FINGERPRINT = "SHA256:54jwqn6/7U5BRTZ8tEGJcxIqMBYqZZZkao3EcU2ba/Y"
OTHER_SSH_PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP4/RIogqrg1nYtweatRKQdoTYQfpy72GUsEp4iSbYjY desktop"
OTHER_SSH_FINGERPRINT = "SHA256:DSpPr3VkdltX8FFWhqTjXnzQEt7H2mUnqbuv/Iwm3/Q"


@pytest.fixture(autouse=True)
def config_path(tmp_path, monkeypatch):
    """Keep settings away from the user's configuration file."""

    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("galloc.settings.CONFIG_PATH", config_path)
    return config_path


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
