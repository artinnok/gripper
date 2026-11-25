import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from gripper.api_clients.exceptions import APIRequestFailed, InvalidAPIResponse
from gripper.core import storage
from gripper.core.schemas import GPU
from gripper.exceptions import InvalidSSHKey
from gripper.providers.exceptions import GPUUnavailable
from gripper.providers.factory import open_provider
from gripper.settings import Settings
from gripper.tui.expiration import delete_expired_gpu_list
from tests.conftest import OTHER_SSH_PUBLIC_KEY, SSH_PUBLIC_KEY


@pytest.fixture
def settings(tmp_path):
    """Configure the Lambda adapter and keep polling fast."""

    return Settings(lambda_api_key=SecretStr("lambda-test-token"), startup_poll_interval=0.001)


def success(data):
    """Wrap an endpoint response in Lambda's data envelope."""

    return httpx.Response(200, json={"data": data})


def failure(status, code):
    """Build a documented Lambda API error envelope."""

    return httpx.Response(status, json={"error": {"code": code, "message": "Request failed"}})


def test_catalog_filters_instances_and_prices_whole_machine(settings, lambda_entry, lambda_http):
    """Exclude CPU, small-disk and sold-out instances, and expose region-filtered capacity."""

    catalog = {}
    for name in ["cpu", "small-disk", "sold-out", "other", "gpu_8x_h100"]:
        entry = deepcopy(lambda_entry)
        entry["instance_type"]["name"] = name
        catalog[name] = entry
    catalog["cpu"]["instance_type"]["specs"]["gpus"] = 0
    catalog["small-disk"]["instance_type"]["specs"]["storage_gib"] = 8
    catalog["sold-out"]["regions_with_capacity_available"] = []
    catalog["other"]["regions_with_capacity_available"][0]["name"] = "us-south-1"
    catalog["other"]["instance_type"].update(price_cents_per_hour=100, gpu_description="Accelerator")
    lambda_http.side_effect = lambda request: success(catalog)

    with open_provider(settings, "lambda") as provider:
        gpu, other = provider.get_gpu_list()
        assert (gpu.name, gpu.hourly_price, gpu.memory_gb) == ("8x H100 (80 GB SXM5)", 35.92, 80)
        assert (gpu.min_ram_gb, gpu.min_vcpu_count) == (1800, 208)
        assert other.memory_gb is None
        assert provider.get_gpu_list("us-west-1") == [gpu]
        assert [region.id for region in provider.get_region_list()] == ["us-south-1", "us-west-1"]
        assert [region.id for region in provider.get_region_list(gpu.id)] == ["us-west-1"]
        with pytest.raises(GPUUnavailable):
            provider.validate_create(gpu.id, "us-south-1")


def test_launch_selects_registered_name_by_fingerprint_and_handles_lost_capacity(
    settings, tmp_path, lambda_entry, lambda_http
):
    """Select the right key among multiple registrations even when its comment changed."""

    settings.lambda_ssh_public_key = tmp_path / "my.pub"
    settings.lambda_ssh_public_key.write_text(SSH_PUBLIC_KEY)
    keys = [
        {"id": "key1", "name": "other", "public_key": OTHER_SSH_PUBLIC_KEY},
        {"id": "key2", "name": "chosen", "public_key": SSH_PUBLIC_KEY.replace("laptop", "elsewhere")},
    ]
    lambda_http.side_effect = [
        success({"gpu_8x_h100": lambda_entry}),
        success(keys),
        failure(400, "instance-operations/launch/insufficient-capacity"),
    ]
    with open_provider(settings, "lambda") as provider:
        gpu, region, price = provider.validate_create("8x H100 (80 GB SXM5)", "us-west-1")
        with pytest.raises(GPUUnavailable):
            provider.create(uuid4(), gpu, region)
    assert b'"ssh_key_names":["chosen"]' in lambda_http.call_args.args[0].content


@pytest.mark.parametrize(
    "keys, preferred", [([], False), ([SSH_PUBLIC_KEY, OTHER_SSH_PUBLIC_KEY], False), ([OTHER_SSH_PUBLIC_KEY], True)]
)
def test_missing_or_ambiguous_key_prevents_launch(settings, tmp_path, lambda_entry, lambda_http, keys, preferred):
    """A missing or ambiguous identity never reaches a billable endpoint."""

    if preferred:
        settings.lambda_ssh_public_key = tmp_path / "mine.pub"
        settings.lambda_ssh_public_key.write_text(SSH_PUBLIC_KEY)
    lambda_http.side_effect = [
        success({"gpu_8x_h100": lambda_entry}),
        success([{"id": str(index), "name": f"key-{index}", "public_key": key} for index, key in enumerate(keys)]),
    ]
    with open_provider(settings, "lambda") as provider, pytest.raises(InvalidSSHKey):
        provider.validate_create("gpu_8x_h100", "us-west-1")
    assert all(call.args[0].method == "GET" for call in lambda_http.call_args_list)


def test_get_only_discards_confirmed_absence(settings, lambda_http):
    """Keep local tracking when a URL, key or response is wrong."""

    lambda_http.side_effect = [
        failure(404, "global/not-found"),
        failure(403, "global/forbidden"),
        success({"status": "active"}),
        failure(404, "global/object-does-not-exist"),
    ]
    with open_provider(settings, "lambda") as provider:
        for error in (APIRequestFailed, APIRequestFailed, InvalidAPIResponse):
            with pytest.raises(error):
                provider.get("instance1")
        assert provider.get("instance1") is None


def test_termination_retries_wait_without_resubmitting_and_redacts_notebook_urls(
    settings, lambda_entry, lambda_http, caplog
):
    """An interrupted termination can be resumed without losing the mapping or leaking notebook access."""

    machine = {
        "id": "instance1",
        "region": lambda_entry["regions_with_capacity_available"][0],
        "instance_type": lambda_entry["instance_type"],
        "status": "terminating",
        "first_healthy": None,
        "jupyter_url": "https://example.test/?token=notebook-secret",
    }
    lambda_http.side_effect = [
        success(machine),
        failure(500, "service/error"),
        success(machine),
        success(machine | {"status": "terminated"}),
    ]
    caplog.set_level(logging.DEBUG, logger="gripper.api_clients.diagnostics")
    with open_provider(settings, "lambda") as provider:
        with pytest.raises(APIRequestFailed):
            provider.delete("instance1")
        provider.delete("instance1")
    assert all(call.args[0].method == "GET" for call in lambda_http.call_args_list)
    assert "jupyter_url" in caplog.text and "notebook-secret" not in caplog.text
    assert "lambda-test-token" not in caplog.text


def test_ttl_retries_until_provider_confirms_termination(tmp_path, settings, lambda_entry, lambda_http):
    """A failed expiration pass keeps the mapping; the next pass can finish an accepted termination."""

    settings.database_path = tmp_path / "state.db"
    gpu = GPU(provider="lambda", provider_instance_id="id1", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    with storage.open_database(settings.database_path) as connection:
        storage.create_gpu(connection, gpu)
    machine = {
        "id": "id1",
        "status": "active",
        "first_healthy": None,
        "region": lambda_entry["regions_with_capacity_available"][0],
        "instance_type": lambda_entry["instance_type"],
    }
    lambda_http.side_effect = [
        success(machine),
        success({"terminated_instances": [{"id": "id1", "status": "terminating"}]}),
        failure(503, "service/unavailable"),
        success(machine | {"status": "terminating"}),
        failure(404, "global/object-does-not-exist"),
    ]
    removed, errors = delete_expired_gpu_list(settings, Event())
    assert not removed and len(errors) == 1
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu(connection, gpu.id).provider_instance_id == "id1"
    assert delete_expired_gpu_list(settings, Event()) == (["gpu-1"], [])
    assert [call.args[0].method for call in lambda_http.call_args_list].count("POST") == 1


def test_startup_waits_for_provider_health_and_ip(settings, lambda_entry, lambda_http):
    """A public address alone does not make a booting or never-healthy GPU ready."""

    machine = {
        "id": "id1",
        "status": "active",
        "region": lambda_entry["regions_with_capacity_available"][0],
        "instance_type": lambda_entry["instance_type"],
        "first_healthy": None,
        "ip": "192.0.2.7",
    }
    healthy = machine | {"first_healthy": "2026-11-19T04:00:00Z"}
    lambda_http.side_effect = [success(machine), success(healthy | {"ip": None}), success(healthy)]
    with open_provider(settings, "lambda") as provider:
        assert provider.get("id1").status == "provisioning"
        assert provider.get("id1").ssh_command is None
        assert provider.get("id1").ssh_command == "ssh ubuntu@192.0.2.7"


@pytest.mark.parametrize(
    "response, expected_calls",
    [
        (
            httpx.Response(
                429, headers={"Retry-After": "0"}, json={"error": {"code": "global/rate-limited", "message": "Wait"}}
            ),
            2,
        ),
        (httpx.Response(503, headers={"Retry-After": "0"}), 1),
        (success({"instance_ids": []}), 1),
    ],
)
def test_launch_retries_only_after_explicit_rate_limit(settings, lambda_http, lambda_entry, response, expected_calls):
    """Never duplicate an uncertain launch even when the response mentions a retry interval."""

    keys = [{"id": "key1", "name": "sole-key", "public_key": SSH_PUBLIC_KEY}]
    lambda_http.side_effect = [
        success({"gpu_8x_h100": lambda_entry}),
        success(keys),
        response,
        success({"instance_ids": ["id1"]}),
    ]
    with open_provider(settings, "lambda") as provider:
        gpu, region, _ = provider.validate_create("gpu_8x_h100", "us-west-1")
        if expected_calls == 2:
            assert provider.create(uuid4(), gpu, region).id == "id1"
        else:
            with pytest.raises((APIRequestFailed, InvalidAPIResponse)):
                provider.create(uuid4(), gpu, region)
    assert lambda_http.call_count == expected_calls + 2
