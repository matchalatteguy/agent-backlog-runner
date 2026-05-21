from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    TODO = "todo"
    RUNNING = "running"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = {TaskStatus.TODO, TaskStatus.RUNNING}
TERMINAL_STATUSES = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}


@dataclass(frozen=True)
class TaskTemplate:
    slug: str
    title: str
    body: str
    role: str = "agent"
    priority: int = 0
    lane: str = "default"
    tags: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskRecord:
    id: str
    title: str
    body: str
    status: TaskStatus
    priority: int = 0
    lane: str = "default"
    tags: tuple[str, ...] = ()
    role: str = "agent"
    workdir: str | None = None
    command: str | None = None
    created_at: int = 0
    updated_at: int = 0
    heartbeat_at: int | None = None


@dataclass(frozen=True)
class TaskEvent:
    id: int
    task_id: str
    event_type: str
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: int = 0


@dataclass(frozen=True)
class BacklogPolicy:
    min_queue_depth: int = 1
    target_queue_depth: int = 3
    max_enqueue_per_cycle: int = 3
    lanes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    avoid_duplicates: bool = True

    def __post_init__(self) -> None:
        if self.min_queue_depth < 0:
            raise ValueError("min_queue_depth must be non-negative")
        if self.target_queue_depth < self.min_queue_depth:
            raise ValueError("target_queue_depth must be >= min_queue_depth")
        if self.max_enqueue_per_cycle < 0:
            raise ValueError("max_enqueue_per_cycle must be non-negative")
