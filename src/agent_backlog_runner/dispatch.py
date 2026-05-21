from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

from .models import TaskStatus
from .store import TaskStore


@dataclass(frozen=True)
class DispatchPolicy:
    max_concurrent_workers: int = 1
    backend: str = "dry-run"
    command: str | None = None
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if self.max_concurrent_workers < 0:
            raise ValueError("max_concurrent_workers must be non-negative")
        if self.backend not in {"dry-run", "subprocess"}:
            raise ValueError("backend must be dry-run or subprocess")
        if self.backend == "subprocess" and not self.command:
            raise ValueError("subprocess backend requires a command template")


@dataclass(frozen=True)
class DispatchResult:
    started: tuple[str, ...]
    skipped_reason: str = ""


def dispatch_ready(store: TaskStore, policy: DispatchPolicy) -> DispatchResult:
    running = len(store.list_tasks((TaskStatus.RUNNING,)))
    slots = max(policy.max_concurrent_workers - running, 0)
    if slots == 0:
        return DispatchResult((), "concurrency cap reached")
    ready = store.list_tasks((TaskStatus.TODO,))[:slots]
    started: list[str] = []
    for task in ready:
        if policy.backend == "dry-run":
            store.add_event(task.id, "dispatch_preview", "dry-run dispatch preview", {})
            started.append(task.id)
            continue
        assert policy.command is not None
        command = policy.command.format(task_id=task.id)
        store.mark_task(task.id, TaskStatus.RUNNING, f"started: {command}")
        completed = subprocess.run(
            shlex.split(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=policy.timeout_seconds,
        )
        if completed.returncode == 0:
            store.mark_task(task.id, TaskStatus.DONE, completed.stdout[-500:])
        else:
            store.mark_task(
                task.id, TaskStatus.FAILED, (completed.stderr or completed.stdout)[-500:]
            )
        started.append(task.id)
    return DispatchResult(tuple(started))
