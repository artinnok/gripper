import logging
import sqlite3
from datetime import UTC, datetime
from threading import Event

from gripper.core.schemas import GPU
from gripper.core.services import delete_gpu
from gripper.core.storage import get_expired_gpu_list, lock_deadlines, open_database
from gripper.core.validators import prepare_expiration
from gripper.exceptions import GripperError
from gripper.providers.factory import open_provider
from gripper.settings import Settings


logger = logging.getLogger(__name__)


def delete_expired_gpu_list(settings: Settings, cancelled: Event) -> tuple[list[str], list[str]]:
    """Delete overdue GPUs in one pass; a failed GPU keeps its mapping for the next pass."""

    deleted_name_list, error_list = [], []
    with open_database(settings.database_path) as connection:
        for candidate in get_expired_gpu_list(connection, datetime.now(UTC)):
            if cancelled.is_set():
                break

            try:
                deleted = _delete_expired_gpu(settings, connection, candidate, cancelled)
            except (GripperError, sqlite3.Error, OSError) as exception:
                logger.debug("TTL deletion failed for %s", candidate.name, exc_info=True)
                error_list.append(f"TTL {candidate.name}: {exception}")
                continue

            if deleted:
                deleted_name_list.append(candidate.name)

    return deleted_name_list, error_list


def _delete_expired_gpu(settings: Settings, connection: sqlite3.Connection, candidate: GPU, cancelled: Event) -> bool:
    """Recheck cancellation and the deadline after waiting for other deadline operations."""

    with lock_deadlines(settings.database_path):
        if cancelled.is_set():
            return False
        gpu = prepare_expiration(connection, candidate.id, datetime.now(UTC))
        if gpu is None:
            return False
        with open_provider(settings, gpu.provider) as provider:
            delete_gpu(connection, provider, gpu)

    return True
