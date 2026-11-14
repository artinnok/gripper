from unittest.mock import Mock, call

import click
import httpx
import pytest
from click.testing import CliRunner

from galloc.api_clients.exceptions import APIRequestFailed
from galloc.providers.base import Provider
from galloc.providers.runpod.schemas import Instance, InstanceStatus
from scripts.smoke_runpod import main, print_catalog, run_lifecycle
from tests.conftest import make_gpu_data


@pytest.fixture
def instance():
    """Provide a running instance for the manual smoke-test workflow."""

    return Instance(
        id="pod-1",
        status=InstanceStatus.RUNNING,
        region="US-KS-2",
        ssh_command=None,
        gpu="H200",
        created_at="2026-06-01T12:00:00Z",
    )


@pytest.fixture
def provider(instance):
    """Provide a ready Pod followed by confirmed absence after cleanup."""

    provider = Mock(spec=Provider)
    provider.create.return_value = instance
    provider.get.side_effect = [instance, None]
    return provider


def test_lifecycle_checks_and_deletes_created_pod(provider, capsys):
    """Complete the scenario and delete the Pod returned by the provider."""

    run_lifecycle(provider, "H200", "US-KS-2", 300)

    gpu_id, gpu, region = provider.create.call_args.args
    assert (gpu, region) == ("H200", "US-KS-2")
    assert provider.delete.call_args_list == [call("pod-1"), call("pod-1")]
    assert "lifecycle completed and deletion confirmed" in capsys.readouterr().out


@pytest.mark.parametrize("failure", [APIRequestFailed("Read failed."), KeyboardInterrupt()])
def test_cleanup_runs_after_failed_read_or_interrupt(provider, failure):
    """Attempt deletion even when polling fails or the user presses Ctrl-C."""

    provider.get.side_effect = [failure, None]

    with pytest.raises(type(failure)):
        run_lifecycle(provider, "H200", "US-KS-2", 300)

    assert provider.delete.call_args_list == [call("pod-1"), call("pod-1")]


def test_uncertain_creation_points_to_provider_console(provider, capsys):
    """Show the Pod name for inspection when creation returns no provider ID."""

    provider.create.side_effect = APIRequestFailed("Create timed out.")

    with pytest.raises(APIRequestFailed, match="Create timed out"):
        run_lifecycle(provider, "H200", "US-KS-2", 300)

    gpu_id, _, _ = provider.create.call_args.args
    assert f"Check galloc-{gpu_id} in the Runpod console." in capsys.readouterr().err
    provider.get.assert_not_called()
    provider.delete.assert_not_called()


def test_startup_timeout_still_deletes_pod(provider, instance, monkeypatch):
    """Bound the startup wait and clean up a Pod that remains provisioning."""

    instance.status = InstanceStatus.PROVISIONING
    monkeypatch.setattr("scripts.smoke_runpod.monotonic", Mock(side_effect=[0, 301]))

    with pytest.raises(click.ClickException, match="did not start"):
        run_lifecycle(provider, "H200", "US-KS-2", 300)

    assert provider.delete.call_args_list == [call("pod-1"), call("pod-1")]


def test_failed_deletion_does_not_report_success(provider, capsys):
    """Keep a cleanup failure visible with the Pod ID in the output."""

    provider.delete.side_effect = APIRequestFailed("Delete failed.")

    with pytest.raises(APIRequestFailed, match="Delete failed"):
        run_lifecycle(provider, "H200", "US-KS-2", 300)

    output = capsys.readouterr().out
    assert "Deleting Pod: pod-1" in output
    assert "lifecycle completed" not in output


def test_catalog_only_reads_api(make_runpod_client, capsys):
    """Check both catalogs without invoking a resource creation endpoint."""

    def handler(request):
        """Serve catalog requests and reject any mutation."""

        assert request.method == "GET"
        if request.url.path == "/v2/catalog/gpus":
            return httpx.Response(
                200, json={"gpus": [make_gpu_data(id="NVIDIA H200", name="H200", secure=True, price={"secure": 5.29})]}
            )
        assert request.url.path == "/v2/catalog/datacenters"
        return httpx.Response(
            200, json={"dataCenters": [{"id": "US-KS-2", "name": "US Kansas 2", "region": "NORTH_AMERICA"}]}
        )

    print_catalog(make_runpod_client(handler))

    output = capsys.readouterr().out
    assert "NVIDIA H200" in output
    assert "US-KS-2" in output
    assert "no resources created" in output


@pytest.mark.parametrize("argument_list", [["--gpu", "H200"], ["--region", "US-KS-2"]])
def test_partial_creation_arguments_fail_before_api_access(argument_list):
    """Require an explicit GPU and region before any live operation."""

    result = CliRunner().invoke(main, argument_list)

    assert result.exit_code == 2
    assert "Supply both" in result.output
