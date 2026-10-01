import json
import shlex
import sys

import pytest

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
    stored = store.get_task(task.id)
    assert stored is not None
    assert stored.status == TaskStatus.DONE
    event_types = [event.event_type for event in store.events(task.id, limit=3)]
    assert event_types[:2] == ["completed", "dispatched"]
    store.close()


def test_subprocess_dispatch_can_use_task_command_and_workdir(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    marker = tmp_path / "marker.txt"
    task = store.create_task(
        title="Write marker",
        body="Body",
        lane="ops",
        role="worker",
        command=(
            'python -c "from pathlib import Path; '
            "Path('marker.txt').write_text('{task_id}|{lane}|{role}', encoding='utf-8')\""
        ),
        workdir=str(tmp_path),
    )

    result = dispatch_ready(store, DispatchPolicy(max_concurrent_workers=1, backend="subprocess"))

    assert result.started == (task.id,)
    stored = store.get_task(task.id)
    assert stored is not None
    assert stored.status == TaskStatus.DONE
    assert marker.read_text(encoding="utf-8") == f"{task.id}|ops|worker"
    store.close()


def test_subprocess_dispatch_rejects_unknown_command_placeholders(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    store.create_task(title="A", body="Body")

    with pytest.raises(ValueError, match="unsupported command placeholder"):
        dispatch_ready(
            store,
            DispatchPolicy(
                max_concurrent_workers=1,
                backend="subprocess",
                command="python -c 'print({unsupported_field})'",
            ),
        )
    store.close()


def test_command_placeholders_are_shell_quoted_before_split(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    argv_path = tmp_path / "argv.json"
    task = store.create_task(title="hello; touch hacked file", body="Body")

    result = dispatch_ready(
        store,
        DispatchPolicy(
            max_concurrent_workers=1,
            backend="subprocess",
            command=(
                'python -c "import json, sys; '
                f"open({str(argv_path)!r}, 'w', encoding='utf-8').write("
                'json.dumps(sys.argv[1:]))" '
                "{title}"
            ),
        ),
    )

    assert result.started == (task.id,)
    assert json.loads(argv_path.read_text(encoding="utf-8")) == ["hello; touch hacked file"]
    assert not (tmp_path / "hacked").exists()
    store.close()


def test_quoted_command_placeholders_do_not_deliver_literal_quotes(tmp_path):
    store = init_store(tmp_path / "tasks.sqlite3")
    argv_path = tmp_path / "argv.json"
    task = store.create_task(title="hello quoted world", body="Body")

    dispatch_ready(
        store,
        DispatchPolicy(
            max_concurrent_workers=1,
            backend="subprocess",
            command=(
                'python -c "import json, sys; '
                f"open({str(argv_path)!r}, 'w', encoding='utf-8').write("
                'json.dumps(sys.argv[1:]))" '
                '"{title}"'
            ),
        ),
    )

    assert json.loads(argv_path.read_text(encoding="utf-8")) == [task.title]
    store.close()


def test_worker_timeout_fails_task_and_continues_batch(tmp_path):
    with init_store(tmp_path / "tasks.sqlite3") as store:
        slow = store.create_task(
            title="slow",
            body="",
            priority=2,
            command=shlex.join([sys.executable, "-c", "import time; time.sleep(10)"]),
        )
        good = store.create_task(
            title="good",
            body="",
            command=shlex.join([sys.executable, "-c", "print('ok')"]),
        )
        result = dispatch_ready(
            store,
            DispatchPolicy(max_concurrent_workers=2, backend="subprocess", timeout_seconds=1),
        )
        assert result.started == (slow.id, good.id)
        assert store.get_task(slow.id).status == TaskStatus.FAILED
        assert store.events(slow.id)[0].message == "worker timed out after 1s"
        assert store.get_task(good.id).status == TaskStatus.DONE


def test_missing_executable_does_not_leave_running_task(tmp_path):
    with init_store(tmp_path / "tasks.sqlite3") as store:
        task = store.create_task(title="bad command", body="", command="nonexistent-worker-123456")
        dispatch_ready(store, DispatchPolicy(backend="subprocess"))
        assert store.get_task(task.id).status == TaskStatus.FAILED
        assert "worker could not start" in store.events(task.id)[0].message


def test_completion_preserves_operator_cancellation(tmp_path, monkeypatch):
    import subprocess

    with init_store(tmp_path / "tasks.sqlite3") as store:
        task = store.create_task(title="cancelled", body="", command="worker")

        def complete_after_cancellation(*args, **kwargs):
            store.mark_task(task.id, TaskStatus.CANCELLED, "operator cancelled")
            return subprocess.CompletedProcess(args[0], returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(subprocess, "run", complete_after_cancellation)
        dispatch_ready(store, DispatchPolicy(backend="subprocess"))
        assert store.get_task(task.id).status == TaskStatus.CANCELLED
        assert store.events(task.id)[0].event_type == "cancelled"
