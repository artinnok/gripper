import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from gripper.core import storage
from gripper.core.exceptions import GPUNotFound


def test_only_mapping_and_deadline_survive_reopening(tmp_path, gpu):
    """Persist identity and TTL without caching any provider-owned fields."""

    path = tmp_path / "state.db"
    with storage.open_database(path) as connection:
        storage.create_gpu(connection, gpu)
        column_list = [row["name"] for row in connection.execute("PRAGMA table_info(gpus)")]
        assert set(column_list) == {"id", "number", "provider", "provider_instance_id", "expires_at"}
    with storage.open_database(path) as connection:
        assert storage.get_gpu(connection, gpu.name) == gpu
        assert storage.get_gpu(connection, gpu.id) == gpu


def test_independent_updates_do_not_overwrite_each_other(tmp_path, gpu):
    """Linking a provider ID must not overwrite a deadline saved by another connection."""

    path = tmp_path / "state.db"
    with storage.open_database(path) as writer, storage.open_database(path) as reader:
        storage.create_gpu(writer, gpu)
        extended = gpu.model_copy(update={"expires_at": gpu.expires_at + timedelta(days=1)})
        storage.update_gpu_expiration(reader, extended)
        gpu.provider_instance_id = "another-instance"
        storage.update_gpu_instance(writer, gpu)
        saved = storage.get_gpu(reader, gpu.name)
        assert saved.expires_at == extended.expires_at
        assert saved.provider_instance_id == "another-instance"


def test_duplicate_mapping_is_not_replaced(connection, gpu):
    """Keep the original mapping when the same creation ID is submitted twice."""

    storage.create_gpu(connection, gpu)
    with pytest.raises(sqlite3.IntegrityError):
        storage.create_gpu(connection, gpu.model_copy(update={"provider_instance_id": "other"}))
    assert storage.get_gpu(connection, gpu.id) == gpu


def test_missing_mapping(connection):
    """Report unknown names and UUIDs explicitly."""

    for reference in ["gpu-99", uuid4()]:
        with pytest.raises(GPUNotFound):
            storage.get_gpu(connection, reference)


@pytest.mark.parametrize("microseconds,expected", [(-1, True), (0, True), (1, False)])
def test_expired_deadlines_use_utc_and_latest_extension(connection, gpu, microseconds, expected):
    """Select inclusive deadlines using UTC and exclude newly extended mappings."""

    now = datetime(2026, 1, 1, 17, tzinfo=timezone(timedelta(hours=4)))
    gpu.expires_at = now + timedelta(microseconds=microseconds)
    storage.create_gpu(connection, gpu)
    assert storage.get_expired_gpu_list(connection, now) == ([gpu] if expected else [])
    gpu.expires_at += timedelta(hours=1)
    storage.update_gpu_expiration(connection, gpu)
    assert storage.get_expired_gpu_list(connection, now) == []
