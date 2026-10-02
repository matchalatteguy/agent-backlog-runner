from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

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
    ready_tasks: int = 0
    delayed_tasks: int = 0
    attempt_outcomes: dict[str, int] = field(default_factory=dict)
    recent_attempts: tuple[dict[str, Any], ...] = ()


def get_status_snapshot(
    store: TaskStore, stale_after: int = 1800, event_limit: int = 10
) -> StatusSnapshot:
    tasks = store.list_tasks()
    counts = {status.value: 0 for status in TaskStatus}
    now = int(time.time())
    stale: list[StaleTask] = []
    attempts = store.attempts()
    outcomes: dict[str, int] = {}
    for attempt in attempts:
        outcomes[attempt["outcome"]] = outcomes.get(attempt["outcome"], 0) + 1
    for task in tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
        if task.status == TaskStatus.RUNNING:
            basis = task.heartbeat_at or task.updated_at
            age = now - basis
            if age > stale_after:
                stale.append(StaleTask(task, age))
    return StatusSnapshot(
        counts=counts,
        active_workers=sum(attempt["finished_at"] is None for attempt in attempts)
        + sum(
            task.status == TaskStatus.RUNNING
            and not any(
                attempt["task_id"] == task.id and attempt["finished_at"] is None
                for attempt in attempts
            )
            for task in tasks
        ),
        stale_tasks=tuple(stale),
        recent_events=tuple(store.events(limit=event_limit)),
        ready_tasks=sum(
            task.status == TaskStatus.TODO and task.not_before <= time.time() for task in tasks
        ),
        delayed_tasks=sum(
            task.status == TaskStatus.TODO and task.not_before > time.time() for task in tasks
        ),
        attempt_outcomes=outcomes,
        recent_attempts=tuple(attempts[:event_limit]),
    )


def format_snapshot(snapshot: StatusSnapshot) -> str:
    lines = ["status count"]
    for key in sorted(snapshot.counts):
        lines.append(f"{key} {snapshot.counts[key]}")
    lines.append(f"active_workers {snapshot.active_workers}")
    lines.append(f"stale_tasks {len(snapshot.stale_tasks)}")
    lines.append(f"ready_tasks {snapshot.ready_tasks}")
    lines.append(f"delayed_tasks {snapshot.delayed_tasks}")
    for outcome, count in sorted(snapshot.attempt_outcomes.items()):
        lines.append(f"attempts.{outcome} {count}")
    return "\n".join(lines)
