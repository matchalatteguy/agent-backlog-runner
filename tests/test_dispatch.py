from agent_backlog_runner.dispatch import DispatchPolicy, dispatch_ready
from agent_backlog_runner.models import TaskStatus
from agent_backlog_runner.store import init_store


def test_dry_run_dispatch_caps_workers(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    t1 = store.create_task(title="A", body="Body")
    store.create_task(title="B", body="Body")
    result = dispatch_ready(store, DispatchPolicy(max_concurrent_workers=1))
    assert result.started == (t1.id,)
    assert store.get_task(t1.id).status == TaskStatus.TODO
    assert store.events(t1.id)[0].event_type == "dispatch_preview"
    store.close()


def test_subprocess_dispatch_records_completion(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    task = store.create_task(title="A", body="Body")
    result = dispatch_ready(
        store,
        DispatchPolicy(
            max_concurrent_workers=1,
            backend="subprocess",
            command="python -c \"print('ok {task_id}')\"",
        ),
    )
    assert result.started == (task.id,)
    assert store.get_task(task.id).status == TaskStatus.DONE
    event_types = [event.event_type for event in store.events(task.id, limit=3)]
    assert event_types[:2] == ["completed", "dispatched"]
    store.close()
