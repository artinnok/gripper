from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

from gripper.core.schemas import GPU
from gripper.core.storage import open_database
from gripper.core.validators import prepare_creation
from gripper.providers.base import Provider
from gripper.providers.schemas import Instance, InstanceStatus
from gripper.providers.ssh import SSHConnection


@pytest.fixture
def gpu():
    """Provide only a provider link and a local deletion deadline."""

    return GPU(provider="example", provider_instance_id="instance-1", expires_at=datetime(2026, 1, 1, 13, tzinfo=UTC))


@pytest.fixture
def connection(tmp_path):
    """Open an isolated database for domain operations."""

    with open_database(tmp_path / "state.db") as connection:
        yield connection


@pytest.fixture
def instance():
    """Provide current instance details owned by the provider."""

    return Instance(
        id="instance-1",
        status=InstanceStatus.RUNNING,
        region="test",
        ssh=SSHConnection(destination="root@example.test"),
        gpu="H200",
        hourly_price=3,
        created_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )


@pytest.fixture
def provider(instance):
    """Replace only the external provider boundary for Core tests."""

    provider = Mock(spec=Provider)
    provider.validate_create.return_value = ("H200", "test", Decimal("3"))
    provider.create.return_value = instance
    provider.get.return_value = instance
    return provider


@pytest.fixture
def creation_arguments():
    """Provide one explicit GPU request with a required TTL."""

    return {"provider_name": "example", "gpu": "H200", "region": "test", "ttl": "30m"}


@pytest.fixture
def prepared_creation(connection, provider, creation_arguments):
    """Prepare creation inputs before calling the service."""

    return prepare_creation(connection, provider, **creation_arguments)
