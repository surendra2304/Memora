"""Contract tests for task-envelope identity bounds."""

import pytest
from pydantic import ValidationError

from apps.api.routers.v1_task import TaskEnvelopeModel


def test_task_id_cannot_exceed_persisted_memory_record_limit():
    with pytest.raises(ValidationError):
        TaskEnvelopeModel(task_id="t" * 65)

def test_task_execute_persists_task_id_on_memory_record(client, test_db):
    from storage.relational.models import MemoryRecord

    response = client.post(
        "/v1/task/execute",
        headers={"X-Agent-Name": "friday"},
        json={
            "task_id": "persist-task-01",
            "action": "store",
            "payload": {"content": "task identity persistence marker persist-task-01"},
        },
    )

    assert response.status_code == 200, response.text
    record = test_db.query(MemoryRecord).filter_by(task_id="persist-task-01").one()
    assert record.task_id == "persist-task-01"

