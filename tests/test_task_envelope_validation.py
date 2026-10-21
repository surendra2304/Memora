"""Contract tests for task-envelope identity bounds."""

import pytest
from pydantic import ValidationError

from apps.api.routers.v1_task import TaskEnvelopeModel


def test_task_id_cannot_exceed_persisted_memory_record_limit():
    with pytest.raises(ValidationError):
        TaskEnvelopeModel(task_id="t" * 65)
