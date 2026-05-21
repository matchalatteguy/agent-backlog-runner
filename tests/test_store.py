from agent_backlog_runner.models import TaskStatus
from agent_backlog_runner.store import init_store


def test_schema_initializes_idempotently(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    store = init_store(db)
    store.init_schema()
    task = store.create_task(title="Write note", body="Body", lane="docs", tags=("docs",))
    assert task.status == TaskStatus.TODO
    assert store.events(task.id)[0].event_type == "created"
    store.close()


def test_mark_and_heartbeat_are_visible(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    task = store.create_task(title="Run sample", body="Body")
    store.mark_task(task.id, TaskStatus.RUNNING, "started")
    store.record_heartbeat(task.id, {"step": 1})
    updated = store.get_task(task.id)
    assert updated is not None
    assert updated.status == TaskStatus.RUNNING
    assert updated.heartbeat_at is not None
    assert [event.event_type for event in store.events(task.id, limit=3)] == ["heartbeat", "dispatched", "created"]
    store.close()
