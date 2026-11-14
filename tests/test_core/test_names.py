from uuid import uuid4

from galloc.core import storage


def test_names_are_not_reused_after_refused_creation(tmp_path, gpu):
    """Retain the sequence across removal, reopen, and database compaction."""

    path = tmp_path / "state.db"
    with storage.open_database(path) as connection:
        storage.create_gpu(connection, gpu)
        assert gpu.name == "gpu-1"
        storage.delete_gpu(connection, gpu.id)
        connection.execute("VACUUM")
    with storage.open_database(path) as connection:
        next_gpu = gpu.model_copy(update={"id": uuid4(), "number": None})
        storage.create_gpu(connection, next_gpu)
        assert next_gpu.name == "gpu-2"
