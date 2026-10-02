from __future__ import annotations

import os
import selectors
import shlex
import signal
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .models import TaskRecord, TaskStatus
from .store import TaskStore

_ALLOWED_COMMAND_FIELDS = {"task_id", "title", "lane", "role", "priority", "workdir"}


@dataclass(frozen=True)
class DispatchPolicy:
    max_concurrent_workers: int = 1
    backend: str = "dry-run"
    command: str | None = None
    timeout_seconds: float = 60
    drain: bool = False
    log_root: Path | None = None
    max_log_bytes: int = 1048576

    def __post_init__(self) -> None:
        if type(self.max_concurrent_workers) is not int or self.max_concurrent_workers < 0:
            raise ValueError("max_concurrent_workers must be a non-negative integer")
        if self.backend not in {"dry-run", "subprocess"}:
            raise ValueError("backend must be dry-run or subprocess")
        if not 0 < self.timeout_seconds <= 86400:
            raise ValueError("timeout_seconds must be positive and at most 86400")
        if type(self.max_log_bytes) is not int or not 1 <= self.max_log_bytes <= 67108864:
            raise ValueError("max_log_bytes must be from 1 to 67108864 per stream")


@dataclass(frozen=True)
class DispatchResult:
    started: tuple[str, ...]
    skipped_reason: str = ""
    completed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    cancelled: tuple[str, ...] = ()


def _command_fields(task: TaskRecord) -> dict[str, str]:
    return {
        "task_id": task.id,
        "title": task.title,
        "lane": task.lane,
        "role": task.role,
        "priority": str(task.priority),
        "workdir": task.workdir or "",
    }


def _render_template_part(part: str, fields: dict[str, str]) -> str:
    for _, field_name, _, _ in string.Formatter().parse(part):
        if field_name is not None and field_name not in _ALLOWED_COMMAND_FIELDS:
            raise ValueError(f"unsupported command placeholder: {field_name}")
    return part.format_map(fields)


def render_command_argv(template: str, task: TaskRecord) -> tuple[str, ...]:
    """Split quoting before substitution; task values cannot create new argv tokens."""
    argv = tuple(
        _render_template_part(part, _command_fields(task)) for part in shlex.split(template)
    )
    if not argv or not argv[0]:
        raise ValueError("worker command must contain an executable")
    return argv


def render_command_template(template: str, task: TaskRecord) -> str:
    return shlex.join(render_command_argv(template, task))


def _command_argv_for_task(task: TaskRecord, policy: DispatchPolicy) -> tuple[str, ...]:
    if policy.command:
        return render_command_argv(policy.command, task)
    if task.command_argv:
        return task.command_argv
    if not task.command:
        raise ValueError("subprocess backend requires a policy command or per-task command")
    return render_command_argv(task.command, task)


@dataclass
class _Log:
    file: BinaryIO
    limit: int
    observed: int = 0
    written: int = 0
    tail: bytes = b""

    def write(self, data: bytes) -> None:
        self.observed += len(data)
        self.tail = (self.tail + data)[-500:]
        retained = data[: max(0, self.limit - self.written)]
        if retained:
            self.file.write(retained)
            self.file.flush()
            self.written += len(retained)


@dataclass
class _Worker:
    task: TaskRecord
    attempt: dict[str, Any]
    process: subprocess.Popen
    started: float
    timeout: float
    stdout: _Log
    stderr: _Log
    last_heartbeat: float = 0


class _SpawnFailure(Exception):
    """An ordinary launch failure after owned-process cleanup succeeded."""


def _note_cleanup_failure(
    store: TaskStore, task_id: str, original: BaseException, cleanup_error: BaseException
) -> None:
    note = f"cleanup failed for {task_id}: {cleanup_error}"
    original.add_note(note)
    try:
        sys.stderr.write(note + "; inspect its unfinished attempt before recovery\n")
    except Exception:
        pass
    try:
        store.add_event(task_id, "cleanup_error", note, {})
    except Exception:
        pass


_HAS_WAITID = all(hasattr(os, name) for name in ("waitid", "WNOWAIT", "P_PID"))


def _require_default_child_handler() -> None:
    if os.name == "posix" and signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise ValueError(
            "subprocess dispatch requires the default SIGCHLD handler; "
            "ignored/custom child reapers cannot retain process-group ownership"
        )


def _exit_ready(process: subprocess.Popen) -> bool:
    if os.name == "posix" and _HAS_WAITID:
        # Keep the leader unreaped until group cleanup: its PID cannot be reused.
        result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        return result is not None
    return process.poll() is not None


def _confirm_unreaped_child(process: subprocess.Popen) -> None:
    _require_default_child_handler()
    try:
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError as exc:
        raise RuntimeError(
            "worker leader was already reaped; refusing process-group signals"
        ) from exc


def _stop_owned_process(process: subprocess.Popen) -> None:
    """Signal only this coordinator's unreaped child/session, never a stored PID."""
    if process.returncode is not None:
        return
    if os.name == "posix":
        _require_default_child_handler()
        if _HAS_WAITID:
            _confirm_unreaped_child(process)
        elif process.poll() is not None:
            # Older interpreters cannot retain an exited leader. Do not signal
            # its former group after poll has reaped it (or reported ECHILD).
            return
        _signal_owned_group(process.pid, signal.SIGTERM)
        # Do not reap between TERM and KILL; retain the leader's PID ownership.
        time.sleep(0.1)
        if _HAS_WAITID:
            _confirm_unreaped_child(process)
        _signal_owned_group(process.pid, signal.SIGKILL)
    else:
        process.terminate()
        time.sleep(0.1)
        if process.poll() is None:
            process.kill()
    process.wait(timeout=5)


def _signal_owned_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin returns EPERM for groups containing only unreaped zombies.
        # Keep the leader unreaped; inspect only this owned group, not PID names.
        if sys.platform != "darwin":
            raise
        result = subprocess.run(
            ["ps", "-o", "stat=", "-g", str(pgid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        states = result.stdout.split()
        if (
            result.returncode != 0
            or not states
            or any(state[0] not in {"Z", "X"} for state in states)
        ):
            raise


def _read_pipe(pipe: BinaryIO, log: _Log, *, limit: int = 128) -> None:
    for _ in range(limit):
        try:
            data = os.read(pipe.fileno(), 65536)
        except BlockingIOError:
            break
        if not data:
            break
        log.write(data)


def _finish_worker(
    store: TaskStore,
    selector: selectors.BaseSelector,
    worker: _Worker,
    outcome: str,
    message: str | None = None,
) -> None:
    for pipe, log in (
        (worker.process.stdout, worker.stdout),
        (worker.process.stderr, worker.stderr),
    ):
        assert pipe is not None
        _read_pipe(pipe, log)
        try:
            selector.unregister(pipe)
        except KeyError:
            pass
        pipe.close()
        log.file.close()
    store.finish_attempt(
        worker.attempt["claim_token"],
        outcome,
        message
        if message is not None
        else (
            worker.stdout.tail.decode("utf-8", "replace")
            if outcome == "done"
            else f"worker exited {worker.process.returncode}: "
            + (worker.stderr.tail or worker.stdout.tail).decode("utf-8", "replace")
        ),
        exit_code=worker.process.returncode,
        stdout_bytes=worker.stdout.observed,
        stderr_bytes=worker.stderr.observed,
        stdout_truncated=worker.stdout.observed > worker.stdout.written,
        stderr_truncated=worker.stderr.observed > worker.stderr.written,
    )


def _spawn(
    store: TaskStore,
    selector: selectors.BaseSelector,
    task: TaskRecord,
    attempt: dict[str, Any],
    policy: DispatchPolicy,
) -> _Worker | None:
    token = attempt["claim_token"]
    root = (policy.log_root or store.path.resolve().parent / "logs").resolve()
    logs: list[BinaryIO] = []
    process = None
    try:
        directory = root / task.id / f"{attempt['attempt_no']:04}-{token[:12]}"
        directory.mkdir(parents=True, exist_ok=False)
        stdout_path, stderr_path = directory / "stdout.log", directory / "stderr.log"
        for path in (stdout_path, stderr_path):
            logs.append(path.open("xb"))
        store.bind_attempt(token, stdout_path=str(stdout_path), stderr_path=str(stderr_path))
        environment = {
            **os.environ,
            "AGENT_BACKLOG_TASK_ID": task.id,
            "AGENT_BACKLOG_ATTEMPT": str(attempt["attempt_no"]),
        }
        process = subprocess.Popen(
            attempt["argv"],
            cwd=task.workdir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            env=environment,
        )
        store.bind_attempt(
            token, worker_pid=process.pid, process_group=process.pid if os.name == "posix" else None
        )
        worker = _Worker(
            task,
            attempt,
            process,
            time.monotonic(),
            task.timeout_seconds or policy.timeout_seconds,
            _Log(logs[0], policy.max_log_bytes),
            _Log(logs[1], policy.max_log_bytes),
        )
        for pipe, log in ((process.stdout, worker.stdout), (process.stderr, worker.stderr)):
            assert pipe is not None
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, log)
        return worker
    except BaseException as original:
        cleanup_failed = False
        if process is not None:
            try:
                _stop_owned_process(process)
            except BaseException as cleanup_error:
                cleanup_failed = True
                _note_cleanup_failure(store, task.id, original, cleanup_error)
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    try:
                        selector.unregister(pipe)
                    except KeyError:
                        pass
                    try:
                        pipe.close()
                    except BaseException as cleanup_error:
                        cleanup_failed = True
                        _note_cleanup_failure(store, task.id, original, cleanup_error)
        for log in logs:
            try:
                log.close()
            except BaseException as cleanup_error:
                cleanup_failed = True
                _note_cleanup_failure(store, task.id, original, cleanup_error)
        if not cleanup_failed:
            if isinstance(original, (OSError, ValueError)):
                raise _SpawnFailure(str(original)) from original
            try:
                store.finish_attempt(token, "interrupted", "launch interrupted; retry explicitly")
            except BaseException as cleanup_error:
                _note_cleanup_failure(store, task.id, original, cleanup_error)
        raise


def dispatch_ready(store: TaskStore, policy: DispatchPolicy) -> DispatchResult:
    """Run a bounded snapshot, concurrently; --drain processes its remaining ready tasks."""
    if os.name != "posix" and policy.backend == "subprocess":
        raise ValueError(
            "subprocess dispatch requires POSIX; preview and queue commands are portable"
        )
    if policy.backend == "subprocess":
        _require_default_child_handler()
    slots = max(policy.max_concurrent_workers - len(store.list_tasks((TaskStatus.RUNNING,))), 0)
    if slots == 0:
        return DispatchResult((), "concurrency cap reached")
    ready = store.list_tasks((TaskStatus.TODO,))
    if not policy.drain:
        ready = [task for task in ready if task.not_before <= time.time()][:slots]
    if policy.backend == "dry-run":
        ready = [task for task in ready if task.not_before <= time.time()]
        for task in ready:
            store.add_event(task.id, "dispatch_preview", "dry-run dispatch preview", {})
        return DispatchResult(tuple(task.id for task in ready))
    # Validate all selected command syntax before claiming or starting any work.
    commands = {task.id: _command_argv_for_task(task, policy) for task in ready}
    pending = {task.id: task for task in ready}
    active: dict[str, _Worker] = {}
    started: list[str] = []
    completed: list[str] = []
    failed: list[str] = []
    cancelled: list[str] = []
    skipped = ""
    with selectors.DefaultSelector() as selector:
        try:
            while pending or active:
                # Stop cancellations before attempting to fill vacated slots.
                for task_id, worker in list(active.items()):
                    task = store.get_task(task_id)
                    ended = _exit_ready(worker.process)
                    outcome, message = None, None
                    if not task or task.status != TaskStatus.RUNNING:
                        _stop_owned_process(worker.process)
                        outcome, message = (
                            "cancelled",
                            "worker stopped after operator status change",
                        )
                    elif ended:
                        if os.name == "posix" and _HAS_WAITID:
                            _stop_owned_process(worker.process)
                        else:
                            worker.process.wait()
                        outcome = "done" if worker.process.returncode == 0 else "failed"
                    elif time.monotonic() - worker.started >= worker.timeout:
                        _stop_owned_process(worker.process)
                        outcome, message = "timeout", f"worker timed out after {worker.timeout:g}s"
                    if outcome:
                        _finish_worker(store, selector, worker, outcome, message)
                        (
                            completed
                            if outcome == "done"
                            else cancelled
                            if outcome == "cancelled"
                            else failed
                        ).append(task_id)
                        del active[task_id]
                    elif time.monotonic() - worker.last_heartbeat >= 0.5:
                        store.heartbeat_attempt(worker.attempt["claim_token"])
                        worker.last_heartbeat = time.monotonic()
                for task_id in list(pending):
                    if len(active) >= policy.max_concurrent_workers:
                        break
                    task = store.get_task(task_id)
                    if not task or task.status != TaskStatus.TODO:
                        del pending[task_id]
                        continue
                    if task.not_before > time.time():
                        continue
                    attempt = store.claim_attempt(
                        task_id, policy.max_concurrent_workers, commands[task_id]
                    )
                    if attempt is None:
                        if task.attempt_count >= task.max_attempts:
                            del pending[task_id]
                            skipped = "some queued tasks exhausted their attempt budget"
                        continue
                    del pending[task_id]
                    started.append(task_id)
                    try:
                        worker = _spawn(store, selector, task, attempt, policy)
                        assert worker is not None
                        active[task_id] = worker
                    except _SpawnFailure as exc:
                        store.finish_attempt(
                            attempt["claim_token"], "spawn_error", f"worker could not start: {exc}"
                        )
                        failed.append(task_id)
                if (
                    pending
                    and not active
                    and all(
                        store.get_task(task_id).not_before <= time.time() for task_id in pending
                    )
                ):
                    skipped = "concurrency capacity held by another or unrecovered attempt"
                    break
                for key, _mask in selector.select(timeout=0.05):
                    _read_pipe(key.fileobj, key.data, limit=4)
        except BaseException as original:
            for worker in active.values():
                try:
                    _stop_owned_process(worker.process)
                    _finish_worker(
                        store,
                        selector,
                        worker,
                        "interrupted",
                        "dispatcher interrupted; retry explicitly",
                    )
                except BaseException as cleanup_error:
                    _note_cleanup_failure(store, worker.task.id, original, cleanup_error)
                    for pipe, log in (
                        (worker.process.stdout, worker.stdout),
                        (worker.process.stderr, worker.stderr),
                    ):
                        assert pipe is not None
                        try:
                            selector.unregister(pipe)
                        except KeyError:
                            pass
                        for handle in (pipe, log.file):
                            try:
                                handle.close()
                            except BaseException as close_error:
                                _note_cleanup_failure(store, worker.task.id, original, close_error)
            raise
    return DispatchResult(
        tuple(started), skipped, tuple(completed), tuple(failed), tuple(cancelled)
    )
