from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from gripper.core.schemas import GPU


def test_gpu_normalizes_timestamps():
    """Normalize initial deadlines and later changes to UTC."""

    local_timezone = timezone(timedelta(hours=4))
    gpu = GPU(
        provider="example",
        expires_at=datetime(2026, 1, 1, 17, tzinfo=local_timezone),
    )

    assert gpu.expires_at == datetime(2026, 1, 1, 13, tzinfo=UTC)
    assert gpu.expires_at.tzinfo is UTC

    gpu.expires_at = datetime(2026, 1, 1, 18, tzinfo=local_timezone)

    assert gpu.expires_at == datetime(2026, 1, 1, 14, tzinfo=UTC)
    assert gpu.expires_at.tzinfo is UTC


@pytest.mark.parametrize("field", ["expires_at"])
def test_gpu_rejects_naive_timestamps(gpu, field):
    """Require a timezone when loading GPU data or changing its dates."""

    data = gpu.model_dump()
    data[field] = datetime(2026, 1, 1)

    with pytest.raises(ValidationError, match="timezone"):
        GPU.model_validate(data)

    with pytest.raises(ValidationError, match="timezone"):
        setattr(gpu, field, data[field])
