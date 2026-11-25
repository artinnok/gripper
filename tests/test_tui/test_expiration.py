from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import httpx
import pytest

from gripper.core import storage
from gripper.core.exceptions import GPUNotFound
from gripper.tui import expiration, operations
from gripper.tui.expiration import delete_expired_gpu_list


@pytest.mark.parametrize("failure_status", [403, 500])
def test_failed_deletion_keeps_mapping_and_continues(settings, expired_gpu, runpod_http, failure_status):
    """Delete other overdue GPUs after a failure and reconcile a lost response on the next pass."""

    other_gpu = expired_gpu.model_copy(update={"id": uuid4(), "provider_instance_id": "another-pod"})
    with storage.open_database(settings.database_path) as connection:
        storage.create_gpu(connection, other_gpu)
    runpod_http.side_effect = [httpx.Response(failure_status), httpx.Response(204), httpx.Response(404)]

    deleted_name_list, error_list = delete_expired_gpu_list(settings, Event())

    assert deleted_name_list == [other_gpu.name]
    (error,) = error_list
    assert expired_gpu.name in error
    assert str(failure_status) in error
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == [expired_gpu]

    assert delete_expired_gpu_list(settings, Event()) == ([expired_gpu.name], [])
    assert delete_expired_gpu_list(settings, Event()) == ([], [])
    assert runpod_http.call_count == 3
    assert all(call.args[0].method == "DELETE" for call in runpod_http.call_args_list)


def test_future_and_unlinked_gpus_are_not_deleted(settings, gpu, runpod_http):
    """Keep future deadlines and unresolved creation reservations untouched."""

    reservation = gpu.model_copy(
        update={"id": uuid4(), "provider_instance_id": None, "expires_at": datetime.now(UTC) - timedelta(days=1)}
    )
    with storage.open_database(settings.database_path) as connection:
        storage.create_gpu(connection, reservation)

    assert delete_expired_gpu_list(settings, Event()) == ([], [])
    runpod_http.assert_not_called()
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == [gpu, reservation]


@pytest.mark.parametrize("change", ["extend", "remove"])
def test_deadline_is_reloaded_before_deletion(settings, expired_gpu, runpod_http, monkeypatch, change):
    """Ignore an outdated candidate when another command already extended or removed it."""

    def select_then_change(connection, now):
        """Apply another connection's change after the initial candidate scan."""

        gpu_list = storage.get_expired_gpu_list(connection, now)
        with storage.open_database(settings.database_path) as writer, storage.transaction(writer):
            if change == "extend":
                extended = expired_gpu.model_copy(update={"expires_at": now + timedelta(hours=1)})
                storage.update_gpu_expiration(writer, extended)
            else:
                storage.delete_gpu(writer, expired_gpu.id)
        return gpu_list

    monkeypatch.setattr(expiration, "get_expired_gpu_list", select_then_change)

    assert delete_expired_gpu_list(settings, Event()) == ([], [])
    runpod_http.assert_not_called()


def test_extension_cannot_report_success_after_deletion_started(settings, expired_gpu, pod_data, runpod_http):
    """Serialize the final deadline update with a deletion already sent to the provider."""

    deletion_started, extension_started, allow_deletion = Event(), Event(), Event()

    def handler(request):
        """Hold deletion open while extension receives a still-running provider response."""

        if request.method == "DELETE":
            deletion_started.set()
            assert allow_deletion.wait(5)
            return httpx.Response(204)
        extension_started.set()
        return httpx.Response(200, json=pod_data)

    runpod_http.side_effect = handler
    with ThreadPoolExecutor() as executor:
        deletion = executor.submit(delete_expired_gpu_list, settings, Event())
        try:
            assert deletion_started.wait(5)
            extension = executor.submit(operations.extend, settings, expired_gpu.id, "1h")
            assert extension_started.wait(5)
            assert not extension.done()
        finally:
            allow_deletion.set()
        assert deletion.result() == ([expired_gpu.name], [])
        with pytest.raises(GPUNotFound):
            extension.result()


def test_deletion_does_not_block_saving_another_gpu(settings, expired_gpu, runpod_http):
    """Allow creation to save its returned provider ID while a TTL request is slow."""

    deletion_started, allow_deletion = Event(), Event()
    new_gpu = expired_gpu.model_copy(
        update={"id": uuid4(), "provider_instance_id": None, "expires_at": datetime.now(UTC) + timedelta(hours=1)}
    )

    def handler(request):
        """Hold only the remote deletion, leaving the database available for creation."""

        deletion_started.set()
        assert allow_deletion.wait(5)
        return httpx.Response(204)

    runpod_http.side_effect = handler
    with ThreadPoolExecutor() as executor:
        deletion = executor.submit(delete_expired_gpu_list, settings, Event())
        try:
            assert deletion_started.wait(5)
            with storage.open_database(settings.database_path) as connection:
                storage.create_gpu(connection, new_gpu)
                new_gpu.provider_instance_id = "new-pod"
                storage.update_gpu_instance(connection, new_gpu)
        finally:
            allow_deletion.set()
        assert deletion.result() == ([expired_gpu.name], [])

    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == [new_gpu]


def test_cancellation_while_waiting_for_deadline_lock_prevents_deletion(
    settings, expired_gpu, runpod_http, monkeypatch
):
    """Keep the GPU when exit cancels a TTL pass that has not acquired the deadline lock yet."""

    waiting, cancelled = Event(), Event()
    runpod_http.side_effect = None
    runpod_http.return_value = httpx.Response(204)

    @contextmanager
    def observed_lock(database_path):
        """Signal the lock attempt while preserving real contention with another operation."""

        waiting.set()
        with storage.lock_deadlines(database_path):
            yield

    monkeypatch.setattr(expiration, "lock_deadlines", observed_lock)
    with ThreadPoolExecutor() as executor:
        with storage.lock_deadlines(settings.database_path):
            deletion = executor.submit(delete_expired_gpu_list, settings, cancelled)
            assert waiting.wait(5)
            cancelled.set()
        assert deletion.result(timeout=5) == ([], [])

    runpod_http.assert_not_called()
    with storage.open_database(settings.database_path) as connection:
        assert storage.get_gpu_list(connection) == [expired_gpu]
