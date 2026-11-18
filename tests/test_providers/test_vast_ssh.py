import json

import httpx
import pytest

from galloc.api_clients.exceptions import APIRequestFailed
from galloc.core.ssh_keys import parse_public_key
from galloc.providers.vast.provider import VastProvider
from tests.conftest import OTHER_SSH_PUBLIC_KEY, SSH_FINGERPRINT, SSH_PUBLIC_KEY


def test_key_lifecycle_preserves_other_registrations(make_vast_client):
    """Add one key, recognize duplicate material, and remove only its provider registration."""

    key_list = [{"id": 11, "key": OTHER_SSH_PUBLIC_KEY}]
    mutation_list = []

    def handler(request):
        """Model the account's individual-key API rather than replacing its entire key list."""

        if request.method == "GET":
            return httpx.Response(200, json=key_list)
        mutation_list.append(request)
        if request.method == "POST":
            assert json.loads(request.content) == {"ssh_key": SSH_PUBLIC_KEY}
            key_list.append({"id": 22, "key": SSH_PUBLIC_KEY})
        else:
            assert request.method == "DELETE"
            assert request.url.path == "/api/v0/ssh/22/"
            key_list.pop()
        return httpx.Response(200, json={"success": True})

    provider = VastProvider(make_vast_client(handler), image="example/image", disk_gb=50)
    assert provider.create_ssh_key(parse_public_key(SSH_PUBLIC_KEY))
    assert not provider.create_ssh_key(parse_public_key(SSH_PUBLIC_KEY.replace("laptop", "other comment")))
    assert provider.delete_ssh_key(SSH_FINGERPRINT)
    assert not provider.delete_ssh_key(SSH_FINGERPRINT)
    assert key_list == [{"id": 11, "key": OTHER_SSH_PUBLIC_KEY}]
    assert len(mutation_list) == 2


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_key_read_failure_does_not_register_over_it(make_vast_client, status):
    """Never treat a permission or network error as an empty account key list."""

    def handler(request):
        """Refuse the prerequisite read and reject any attempted write."""

        assert request.method == "GET"
        return httpx.Response(status)

    provider = VastProvider(make_vast_client(handler), image="example/image", disk_gb=50)
    with pytest.raises(APIRequestFailed):
        provider.create_ssh_key(parse_public_key(SSH_PUBLIC_KEY))


@pytest.mark.parametrize("field", ["key", "public_key"])
def test_key_list_reads_either_documented_field(make_vast_client, field):
    """Read the key text under the name used by the key list and the one used by a created key."""

    def handler(request):
        """Return one registered key under the requested field name."""

        return httpx.Response(200, json=[{"id": 11, "user_id": 7, field: SSH_PUBLIC_KEY, "deleted_at": None}])

    provider = VastProvider(make_vast_client(handler), image="example/image", disk_gb=50)
    (ssh_key,) = provider.get_ssh_key_list()
    assert ssh_key.fingerprint == SSH_FINGERPRINT
