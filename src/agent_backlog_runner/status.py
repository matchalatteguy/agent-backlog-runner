from __future__ import annotations

import time
from dataclasses import dataclass

from .models import TaskEvent, TaskRecord, TaskStatus
from .store import TaskStore


@dataclass(frozen=True)
class StaleTask:
    task: TaskRecord
    age_seconds: int


@dataclass(frozen=True)
class StatusSnapshot:
    counts: dict[str, int]
    active_workers: int
    stale_tasks: tuple[StaleTask, ...]
    recent_events: tuple[TaskEvent, ...]


def get_status_snapshot(
    store: TaskStore, stale_after: int = 1800, event_limit: int = 10
) -> StatusSnapshot:
    tasks = store.list_tasks()
    counts = {status.value: 0 for status in TaskStatus}
    now = int(time.time())
    stale: list[StaleTask] = []
    for task in tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
        if task.status == TaskStatus.RUNNING:
            basis = task.heartbeat_at or task.updated_at
            age = now - basis
            if age > stale_after:
                stale.append(StaleTask(task, age))
    return StatusSnapshot(
        counts=counts,
        active_workers=counts.get(TaskStatus.RUNNING.value, 0),
        stale_tasks=tuple(stale),
        recent_events=tuple(store.events(limit=event_limit)),
    )


def format_snapshot(snapshot: StatusSnapshot) -> str:
    lines = ["status count"]
    for key in sorted(snapshot.counts):
        lines.append(f"{key} {snapshot.counts[key]}")
    lines.append(f"active_workers {snapshot.active_workers}")
    lines.append(f"stale_tasks {len(snapshot.stale_tasks)}")
    return "\n".join(lines)
