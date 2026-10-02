from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Timer

import pytest

from agent_backlog_runner.batches import enqueue_batch, load_batch_manifest
from agent_backlog_runner.cli import main
from agent_backlog_runner.dispatch import DispatchPolicy, dispatch_ready
from agent_backlog_runner.models import TaskStatus
from agent_backlog_runner.recovery import recover_task
from agent_backlog_runner.store import init_store


def _task(store, title, code, **kwargs):
    return store.create_task(
        title=title, body="", command_argv=(sys.executable, "-c", code), **kwargs
    )


def test_workers_must_overlap_to_complete(tmp_path):
    """Each command waits for the other: a serial implementation cannot pass."""
    with init_store(tmp_path / "queue.sqlite3") as store:
        for index in (0, 1):
            _task(
                store,
                str(index),
                "import time; from pathlib import Path; "
                f"Path({str(tmp_path / str(index))!r}).write_text('ready'); "
                f"other=Path({str(tmp_path / str(1 - index))!r}); "
                "end=time.monotonic()+2\n"
                "while not other.exists() and time.monotonic()<end: time.sleep(.02)\n"
                "assert other.exists(), 'workers did not overlap'",
                timeout_seconds=3,
            )
        result = dispatch_ready(store, DispatchPolicy(2, "subprocess"))
        assert len(result.completed) == 2
        assert not result.failed


def test_drain_consumes_snapshot_without_repeating_done_work(tmp_path):
    with init_store(tmp_path / "queue.sqlite3") as store:
        for index in range(5):
            _task(store, str(index), "print('completed')")
        first = dispatch_ready(store, DispatchPolicy(2, "subprocess"))
        assert len(first.completed) == 2
        remaining = dispatch_ready(store, DispatchPolicy(2, "subprocess", drain=True))
        assert len(remaining.completed) == 3
        assert len(store.attempts()) == 5
        assert not dispatch_ready(store, DispatchPolicy(2, "subprocess", drain=True)).started


def test_competing_coordinators_respect_capacity_and_unique_attempts(tmp_path):
    db = tmp_path / "queue.sqlite3"
    with init_store(db) as store:
        for index in range(6):
            _task(store, str(index), "import time; time.sleep(.15)")
    barrier = Barrier(2)

    def run():
        with init_store(db) as store:
            barrier.wait(timeout=5)
            return dispatch_ready(store, DispatchPolicy(2, "subprocess", drain=True))

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    with init_store(db) as store:
        dispatch_ready(store, DispatchPolicy(2, "subprocess", drain=True))
        attempts = store.attempts()
        assert len(attempts) == 6
        assert len({item["task_id"] for item in attempts}) == 6
        timeline = [(item["started_at"], 1) for item in attempts]
        timeline += [(item["finished_at"], -1) for item in attempts]
        active = peak = 0
        for _when, change in sorted(timeline):
            active += change
            peak = max(peak, active)
        assert peak <= 2
        assert all(item["outcome"] == "done" for item in attempts)
    assert sum(len(result.started) for result in results) <= 6


def test_large_dual_streams_are_drained_and_files_capped(tmp_path):
    with init_store(tmp_path / "queue.sqlite3") as store:
        task = _task(
            store,
            "large output",
            "import sys; "
            "sys.stdout.buffer.write(b'a'*200000); "
            "sys.stderr.buffer.write(b'b'*200000)",
        )
        result = dispatch_ready(store, DispatchPolicy(1, "subprocess", max_log_bytes=1024))
        assert result.completed == (task.id,)
        attempt = store.attempts(task.id)[0]
        for stream in ("stdout", "stderr"):
            assert attempt[stream + "_bytes"] == 200000
            assert attempt[stream + "_truncated"] == 1
            assert Path(attempt[stream + "_path"]).stat().st_size == 1024


def test_retry_is_explicit_backed_off_and_budgeted(tmp_path):
    db = tmp_path / "queue.sqlite3"
    with init_store(db) as store:
        task = _task(
            store, "fails", "raise SystemExit(7)", max_attempts=2, retry_backoff_seconds=0.2
        )
        dispatch_ready(store, DispatchPolicy(1, "subprocess"))
        assert store.attempts(task.id)[0]["exit_code"] == 7
        assert not dispatch_ready(store, DispatchPolicy(1, "subprocess")).started
        retried = store.retry_task(task.id)
        assert retried.not_before > time.time()
        assert not dispatch_ready(store, DispatchPolicy(1, "dry-run")).started
        dispatch_ready(store, DispatchPolicy(1, "subprocess", drain=True))
        assert store.get_task(task.id).attempt_count == 2
        assert [item["attempt_no"] for item in store.attempts(task.id)] == [2, 1]
        with pytest.raises(ValueError, match="budget"):
            store.retry_task(task.id)


def test_cancelled_attempt_keeps_capacity_until_coordinator_finishes(tmp_path):
    with init_store(tmp_path / "queue.sqlite3") as store:
        first = _task(store, "first", "pass")
        second = _task(store, "second", "pass")
        attempt = store.claim_attempt(first.id, 1, first.command_argv)
        store.request_cancel(first.id)
        assert store.claim_attempt(second.id, 1, second.command_argv) is None
        with pytest.raises(ValueError, match="still active"):
            store.retry_task(first.id)
        store.finish_attempt(attempt["claim_token"], "cancelled", "stopped")
        assert store.claim_attempt(second.id, 1, second.command_argv) is not None


def test_old_attempt_cannot_complete_a_new_attempt(tmp_path):
    with init_store(tmp_path / "queue.sqlite3") as store:
        task = _task(store, "tokens", "pass", retry_backoff_seconds=0)
        first = store.claim_attempt(task.id, 1, task.command_argv)
        store.finish_attempt(first["claim_token"], "failed", "failed")
        store.retry_task(task.id)
        second = store.claim_attempt(task.id, 1, task.command_argv)
        assert not store.finish_attempt(first["claim_token"], "done", "stale completion")
        assert store.get_task(task.id).status == TaskStatus.RUNNING
        assert store.attempt(second["claim_token"])["finished_at"] is None


def test_claimed_dispatcher_crash_requires_explicit_unknown_acknowledgement(tmp_path):
    db = tmp_path / "queue.sqlite3"
    with init_store(db) as store:
        task = _task(store, "crash window", "pass", retry_backoff_seconds=0)
    code = (
        "import os; from agent_backlog_runner.store import init_store; "
        f"s=init_store({str(db)!r}); "
        f"s.claim_attempt({task.id!r},1,('unused',)); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    with init_store(db) as store:
        with pytest.raises(ValueError, match="identity was not recorded"):
            recover_task(store, task.id, stale_after=0)
        recover_task(store, task.id, stale_after=0, acknowledge_unknown=True)
        assert store.get_task(task.id).status == TaskStatus.FAILED
        assert store.attempts(task.id)[0]["outcome"] == "abandoned"
        store.retry_task(task.id)
        assert dispatch_ready(store, DispatchPolicy(1, "subprocess")).completed == (task.id,)
        assert store.get_task(task.id).attempt_count == 2


def test_recovery_never_adopts_or_signals_a_live_pid(tmp_path):
    with init_store(tmp_path / "queue.sqlite3") as store:
        task = _task(store, "live owner", "pass")
        store.claim_attempt(task.id, 1, task.command_argv)
        with pytest.raises(ValueError, match="dispatcher PID still exists"):
            recover_task(store, task.id, stale_after=0, acknowledge_unknown=True)
        assert store.get_task(task.id).status == TaskStatus.RUNNING


@pytest.mark.skipif(os.name != "posix", reason="POSIX inherited child signal disposition")
def test_inherited_ignored_sigchld_refused_before_claiming(tmp_path):
    db = tmp_path / "queue.sqlite3"
    with init_store(db) as store:
        task = _task(store, "ignored child handler", "pass")
    argv = [
        sys.executable,
        "-m",
        "agent_backlog_runner.cli",
        "dispatch",
        "--db",
        str(db),
        "--backend",
        "subprocess",
    ]
    code = (
        "import os,signal; signal.signal(signal.SIGCHLD,signal.SIG_IGN); "
        f"os.execv({sys.executable!r},{argv!r})"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 1
    assert "default SIGCHLD" in result.stderr
    with init_store(db) as store:
        assert store.attempts(task.id) == []
        assert store.get_task(task.id).status == TaskStatus.TODO


@pytest.mark.skipif(
    not all(hasattr(os, name) for name in ("waitid", "WNOWAIT", "P_PID")),
    reason="requires retained-leader completion observation",
)
def test_external_reaper_never_allows_signalling_historical_group(tmp_path, monkeypatch, capsys):
    import agent_backlog_runner.dispatch as dispatch

    reaped = []
    original_exit_ready = dispatch._exit_ready

    def reap(process):
        os.waitpid(process.pid, 0)
        reaped.append(process)
        return original_exit_ready(process)

    def unsafe_signal(*_args):
        pytest.fail("attempted to signal a reaped leader's historical group")

    monkeypatch.setattr(dispatch, "_exit_ready", reap)
    monkeypatch.setattr(dispatch.os, "killpg", unsafe_signal)
    with init_store(tmp_path / "queue.sqlite3") as store:
        task = _task(store, "external reaper", "pass")
        try:
            with pytest.raises(ChildProcessError):
                dispatch_ready(store, DispatchPolicy(1, "subprocess"))
            assert store.attempts(task.id)[0]["finished_at"] is None
            assert "already reaped" in capsys.readouterr().err
        finally:
            for process in reaped:
                process.returncode = 0  # The test itself already reaped this child.
                for pipe in (process.stdout, process.stderr):
                    if pipe:
                        pipe.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_stop_child_process_group(tmp_path, cancel):
    marker = tmp_path / "orphan.txt"
    child = (
        "import time; from pathlib import Path; time.sleep(1); "
        f"Path({str(marker)!r}).write_text('orphan')"
    )
    code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)"
    )
    db = tmp_path / "queue.sqlite3"
    with init_store(db) as store:
        task = _task(store, "group", code)
        timer = None
        if cancel:

            def request():
                with init_store(db) as other:
                    other.request_cancel(task.id)

            timer = Timer(0.2, request)
            timer.start()
        dispatch_ready(store, DispatchPolicy(1, "subprocess", timeout_seconds=0.3))
        if timer:
            timer.join(timeout=5)
        outcome = "cancelled" if cancel else "timeout"
        assert store.attempts(task.id)[0]["outcome"] == outcome
    time.sleep(1.1)
    assert not marker.exists(), "worker's child survived process-group termination"


@pytest.mark.skipif(os.name != "posix", reason="SIGINT")
def test_graceful_interrupt_records_attempt_and_preserves_pending_queue(tmp_path):
    db = tmp_path / "queue.sqlite3"
    with init_store(db) as store:
        first = _task(
            store, "long", "import time; time.sleep(10)", priority=2, retry_backoff_seconds=0
        )
        second = _task(store, "pending", "print('remaining')")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent_backlog_runner.cli",
            "dispatch",
            "--db",
            str(db),
            "--backend",
            "subprocess",
            "--drain",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        end = time.monotonic() + 5
        while time.monotonic() < end:
            with init_store(db) as store:
                attempts = store.attempts(first.id)
                if attempts and attempts[0]["worker_pid"]:
                    break
            time.sleep(0.02)
        else:
            pytest.fail("dispatcher did not start")
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=5)
        assert process.returncode == 130
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    with init_store(db) as store:
        assert store.attempts(first.id)[0]["outcome"] == "interrupted"
        assert store.get_task(second.id).status == TaskStatus.TODO
        result = dispatch_ready(store, DispatchPolicy(1, "subprocess", drain=True))
        assert result.completed == (second.id,)


def _manifest(tmp_path, **task_changes):
    path = tmp_path / "batch.json"
    task = {"id": "unit-tests", "argv": ["{python}", "-c", "print('ok')"], **task_changes}
    path.write_text(json.dumps({"schema_version": 1, "name": "repo-checks", "tasks": [task]}))
    return path


def test_manifest_load_is_idempotent_and_rejects_changed_run(tmp_path):
    path = _manifest(tmp_path)
    with init_store(tmp_path / "queue.sqlite3") as store:
        manifest = load_batch_manifest(path)
        first = enqueue_batch(store, manifest)
        second = enqueue_batch(store, manifest)
        assert first[0].id == second[0].id
        assert len(store.list_tasks()) == 1
        changed = load_batch_manifest(_manifest(tmp_path, title="changed"))
        with pytest.raises(ValueError, match="definition changed"):
            enqueue_batch(store, changed)
        assert len(enqueue_batch(store, changed, run_id="next-run")) == 1
        assert len(store.list_tasks()) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"argv": "python"},
        {"workdir": ".."},
        {"max_attempts": True},
        {"timeout_seconds": 0},
        {"retry_backoff_seconds": -1},
        {"unknown": "typo"},
    ],
)
def test_manifest_rejects_unsafe_or_ambiguous_shapes(tmp_path, changes):
    with pytest.raises(ValueError):
        load_batch_manifest(_manifest(tmp_path, **changes))


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        ("json", '{"schema_version":1,"schema_version":1,"name":"repo-checks","tasks":[]}'),
        ("yaml", "schema_version: 1\nschema_version: 1\nname: repo-checks\ntasks: []\n"),
    ],
)
def test_manifest_rejects_duplicate_fields_before_execution(tmp_path, suffix, content):
    path = tmp_path / f"checks.{suffix}"
    path.write_text(content)
    with pytest.raises(ValueError, match="duplicate"):
        load_batch_manifest(path)


def test_cli_failure_status_attempts_and_logs(tmp_path, capsys):
    db = tmp_path / "queue.sqlite3"
    with init_store(db) as store:
        task = _task(
            store, "failure", "import sys; print('helpful failure',file=sys.stderr); sys.exit(4)"
        )
    assert main(["dispatch", "--db", str(db), "--backend", "subprocess"]) == 1
    capsys.readouterr()
    assert main(["attempts", "--db", str(db), task.id, "--format", "json"]) == 0
    attempt = json.loads(capsys.readouterr().out)[0]
    assert attempt["exit_code"] == 4
    assert main(["logs", "--db", str(db), task.id, "--stream", "stderr", "--tail", "10"]) == 0
    assert capsys.readouterr().out == "l failure\n"


def test_schema_one_migrates_without_changing_legacy_state(tmp_path):
    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""CREATE TABLE schema_meta(
        key TEXT PRIMARY KEY,value TEXT,updated_at INTEGER);
        INSERT INTO schema_meta VALUES('schema_version','1',0);
        CREATE TABLE tasks(id TEXT PRIMARY KEY,title TEXT NOT NULL,
        body TEXT NOT NULL,status TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0,lane TEXT NOT NULL DEFAULT 'default',
        tags TEXT NOT NULL DEFAULT '[]',
        role TEXT NOT NULL DEFAULT 'agent',workdir TEXT,command TEXT,created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,heartbeat_at INTEGER);
        INSERT INTO tasks(id,title,body,status,created_at,updated_at) VALUES
        ('task_abcdef123456','legacy','body','running',0,0);""")
    conn.close()
    with init_store(db) as store:
        assert store.schema_version() == 2
        assert store.get_task("task_abcdef123456").status == TaskStatus.RUNNING
        assert store.attempts() == []
        with pytest.raises(ValueError, match="legacy running"):
            recover_task(store, "task_abcdef123456", stale_after=0)
        recover_task(store, "task_abcdef123456", stale_after=0, acknowledge_unknown=True)


def test_due_work_is_not_held_behind_a_delayed_retry(tmp_path):
    with init_store(tmp_path / "queue.sqlite3") as store:
        delayed = _task(
            store, "delayed", "raise SystemExit(1)", priority=2, retry_backoff_seconds=2
        )
        dispatch_ready(store, DispatchPolicy(1, "subprocess"))
        store.retry_task(delayed.id)
        eligible = _task(store, "eligible", "pass")
        result = dispatch_ready(store, DispatchPolicy(1, "subprocess"))
        assert result.completed == (eligible.id,)
        assert store.get_task(delayed.id).status == TaskStatus.TODO


def test_active_coordinators_cannot_disagree_about_capacity(tmp_path):
    with init_store(tmp_path / "queue.sqlite3") as store:
        first = _task(store, "first", "pass")
        second = _task(store, "second", "pass")
        attempt = store.claim_attempt(first.id, 1, first.command_argv)
        with pytest.raises(ValueError, match="same --max-workers"):
            store.claim_attempt(second.id, 2, second.command_argv)
        store.finish_attempt(attempt["claim_token"], "done", "complete")
        assert store.claim_attempt(second.id, 2, second.command_argv)


def test_capacity_is_reserved_when_legacy_running_work_already_exists(tmp_path):
    with init_store(tmp_path / "queue.sqlite3") as store:
        legacy = _task(store, "manual", "pass")
        store.mark_task(legacy.id, TaskStatus.RUNNING)
        first = _task(store, "first", "pass")
        second = _task(store, "second", "pass")
        assert store.claim_attempt(first.id, 2, first.command_argv)
        with pytest.raises(ValueError, match="same --max-workers"):
            store.claim_attempt(second.id, 3, second.command_argv)


def test_cleanup_failure_does_not_leave_other_owned_workers_running(tmp_path, monkeypatch, capsys):
    import agent_backlog_runner.dispatch as dispatch

    workers = []
    original_spawn, original_stop, original_read = (
        dispatch._spawn,
        dispatch._stop_owned_process,
        dispatch._read_pipe,
    )
    interrupted = False
    failed_cleanup = False

    def spawn(*args, **kwargs):
        worker = original_spawn(*args, **kwargs)
        workers.append(worker)
        return worker

    def read(*args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt("test interruption")
        return original_read(*args, **kwargs)

    def stop(process):
        nonlocal failed_cleanup
        if not failed_cleanup:
            failed_cleanup = True
            raise PermissionError("test cleanup denial")
        return original_stop(process)

    monkeypatch.setattr(dispatch, "_spawn", spawn)
    monkeypatch.setattr(dispatch, "_read_pipe", read)
    monkeypatch.setattr(dispatch, "_stop_owned_process", stop)
    with init_store(tmp_path / "queue.sqlite3") as store:
        first = _task(store, "first", "import time; print('ready',flush=True); time.sleep(10)")
        second = _task(store, "second", "import time; print('ready',flush=True); time.sleep(10)")
        try:
            with pytest.raises(KeyboardInterrupt, match="test interruption"):
                dispatch_ready(store, DispatchPolicy(2, "subprocess"))
            assert store.attempts(first.id)[0]["finished_at"] is None
            assert store.attempts(second.id)[0]["outcome"] == "interrupted"
            assert workers[1].process.returncode is not None
            assert "test cleanup denial" in capsys.readouterr().err
        finally:
            for worker in workers:
                original_stop(worker.process)
                for pipe in (worker.process.stdout, worker.process.stderr):
                    if pipe and not pipe.closed:
                        pipe.close()
                worker.stdout.file.close()
                worker.stderr.file.close()


def test_spawn_cleanup_failure_preserves_unfinished_attempt(tmp_path, monkeypatch, capsys):
    import agent_backlog_runner.dispatch as dispatch

    processes = []
    original_popen, original_stop = dispatch.subprocess.Popen, dispatch._stop_owned_process

    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    def deny_stop(_process):
        raise PermissionError("test launch cleanup denial")

    with init_store(tmp_path / "queue.sqlite3") as store:
        task = _task(store, "launch", "import time; time.sleep(10)")
        original_bind = store.bind_attempt

        def bind(token, **values):
            original_bind(token, **values)
            if "worker_pid" in values:
                raise OSError("test launch binding failure")

        monkeypatch.setattr(store, "bind_attempt", bind)
        monkeypatch.setattr(dispatch.subprocess, "Popen", popen)
        monkeypatch.setattr(dispatch, "_stop_owned_process", deny_stop)
        try:
            with pytest.raises(OSError, match="test launch binding failure"):
                dispatch_ready(store, DispatchPolicy(1, "subprocess"))
            assert store.attempts(task.id)[0]["finished_at"] is None
            assert store.get_task(task.id).status == TaskStatus.RUNNING
            assert "test launch cleanup denial" in capsys.readouterr().err
        finally:
            for process in processes:
                original_stop(process)


def test_interruption_during_spawn_stops_and_records_owned_process(tmp_path, monkeypatch):
    with init_store(tmp_path / "queue.sqlite3") as store:
        task = _task(store, "launch", "import time; time.sleep(10)")
        original_bind = store.bind_attempt

        def bind(token, **values):
            original_bind(token, **values)
            if "worker_pid" in values:
                raise KeyboardInterrupt("test launch interruption")

        monkeypatch.setattr(store, "bind_attempt", bind)
        with pytest.raises(KeyboardInterrupt, match="test launch interruption"):
            dispatch_ready(store, DispatchPolicy(1, "subprocess"))
        attempt = store.attempts(task.id)[0]
        assert attempt["outcome"] == "interrupted"
        assert attempt["finished_at"] is not None
        assert store.get_task(task.id).status == TaskStatus.FAILED


@pytest.mark.skipif(
    not all(hasattr(os, name) for name in ("waitid", "WNOWAIT", "P_PID")),
    reason="requires retained-leader completion observation",
)
def test_normal_exit_cleans_up_inherited_background_group(tmp_path):
    marker = tmp_path / "background.txt"
    child = f"import time; from pathlib import Path; time.sleep(.7); Path({str(marker)!r}).touch()"
    code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
    with init_store(tmp_path / "queue.sqlite3") as store:
        _task(store, "background", code)
        result = dispatch_ready(store, DispatchPolicy(1, "subprocess"))
        assert len(result.completed) == 1
    time.sleep(0.8)
    assert not marker.exists()


def test_maintenance_walkthrough_checks_repairs_and_resumes(tmp_path):
    from agent_backlog_runner.maintenance import run_demo

    result = run_demo(tmp_path / "demo")
    assert {
        key: result[key]
        for key in (
            "checks",
            "initial_completed",
            "resume_failed",
            "repaired_checks",
            "attempts",
            "final_done",
        )
    } == {
        "checks": 3,
        "initial_completed": 1,
        "resume_failed": 1,
        "repaired_checks": 1,
        "attempts": 4,
        "final_done": 3,
    }
    report = Path(result["report"]).read_text()
    assert "2/3" in report
    assert "stderr" in report
    before = json.loads((tmp_path / "demo" / "before-repair.json").read_text())
    assert before["summary"]["counts"]["failed"] == 1
