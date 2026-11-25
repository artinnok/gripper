import fcntl
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID

from gripper.core.exceptions import GPUNotFound
from gripper.core.schemas import GPU
from gripper.core.ttl import as_utc


@contextmanager
def open_database(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Open the local mapping database and create its table if needed."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path, autocommit=True)) as connection:
        connection.row_factory = sqlite3.Row
        _create_table(connection)
        yield connection


def _create_table(connection: sqlite3.Connection) -> None:
    """Store only identity mapping and TTL, without provider-state snapshots."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS gpus (
            number INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL,
            provider_instance_id TEXT,
            expires_at TEXT NOT NULL
        )
        """
    )


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Commit a short local update without holding a transaction across API requests."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


@contextmanager
def lock_deadlines(database_path: Path) -> Iterator[None]:
    """Serialize extension and expiration across processes; closing the lock file releases the lock."""

    with Path(f"{database_path}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def create_gpu(connection: sqlite3.Connection, gpu: GPU) -> None:
    """Reserve a stable local name before submitting the provider request."""

    cursor = connection.execute(
        "INSERT INTO gpus (id, provider, provider_instance_id, expires_at) VALUES (?, ?, ?, ?)",
        (
            str(gpu.id),
            gpu.provider,
            gpu.provider_instance_id,
            gpu.expires_at.isoformat(timespec="microseconds"),
        ),
    )
    gpu.number = cursor.lastrowid


def get_gpu(connection: sqlite3.Connection, gpu_id: UUID | str) -> GPU:
    """Retrieve a mapping by its short name or internal UUID."""

    reference = str(gpu_id)
    if reference.startswith("gpu-"):
        row = connection.execute("SELECT * FROM gpus WHERE number = ?", (reference.removeprefix("gpu-"),)).fetchone()
    else:
        row = connection.execute("SELECT * FROM gpus WHERE id = ?", (reference,)).fetchone()
    if row is None:
        raise GPUNotFound(f"GPU {gpu_id} was not found.")

    return GPU.model_validate(dict(row))


def get_gpu_list(connection: sqlite3.Connection) -> list[GPU]:
    """Return identity mappings in name order; no remote status is stored here."""

    row_list = connection.execute("SELECT * FROM gpus ORDER BY number").fetchall()
    return [GPU.model_validate(dict(row)) for row in row_list]


def get_linked_gpu_map(connection: sqlite3.Connection) -> dict[str, list[GPU]]:
    """Group linked GPUs by provider, excluding unresolved creation reservations."""

    row_list = connection.execute(
        "SELECT * FROM gpus WHERE provider_instance_id IS NOT NULL ORDER BY number"
    ).fetchall()
    gpu_map = {}
    for row in row_list:
        gpu = GPU.model_validate(dict(row))
        gpu_map.setdefault(gpu.provider, []).append(gpu)
    return gpu_map


def update_gpu_instance(connection: sqlite3.Connection, gpu: GPU) -> None:
    """Link the provider instance without changing an independently extended deadline."""

    connection.execute(
        "UPDATE gpus SET provider_instance_id = ? WHERE id = ?",
        (gpu.provider_instance_id, str(gpu.id)),
    )


def update_gpu_expiration(connection: sqlite3.Connection, gpu: GPU) -> None:
    """Save the prepared deletion deadline."""

    connection.execute(
        "UPDATE gpus SET expires_at = ? WHERE id = ?",
        (gpu.expires_at.isoformat(timespec="microseconds"), str(gpu.id)),
    )


def delete_gpu(connection: sqlite3.Connection, gpu_id: UUID) -> None:
    """Remove an obsolete mapping without retaining history or reusing its number."""

    connection.execute("DELETE FROM gpus WHERE id = ?", (str(gpu_id),))


def get_expired_gpu_list(connection: sqlite3.Connection, now: datetime) -> list[GPU]:
    """Select linked deadlines; callers must reload each deadline before deleting."""

    row_list = connection.execute(
        "SELECT * FROM gpus WHERE provider_instance_id IS NOT NULL AND expires_at <= ? ORDER BY expires_at, number",
        (as_utc(now).isoformat(timespec="microseconds"),),
    ).fetchall()
    return [GPU.model_validate(dict(row)) for row in row_list]
