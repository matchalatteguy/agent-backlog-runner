import time

from agent_backlog_runner.models import TaskStatus
from agent_backlog_runner.status import get_status_snapshot
from agent_backlog_runner.store import init_store


def test_status_counts_and_stale_detection(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    task = store.create_task(title="A", body="Body")
    store.mark_task(task.id, TaskStatus.RUNNING, "started")
    store.conn.execute(
        "UPDATE tasks SET heartbeat_at=?, updated_at=? WHERE id=?",
        (int(time.time()) - 100, int(time.time()) - 100, task.id),
    )
    snapshot = get_status_snapshot(store, stale_after=10)
    assert snapshot.counts["running"] == 1
    assert snapshot.stale_tasks[0].task.id == task.id
    store.close()
