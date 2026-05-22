from __future__ import annotations

import shlex
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import TaskRecord, TaskStatus
from .store import TaskStore

_ALLOWED_COMMAND_FIELDS = {"task_id", "title", "lane", "role", "priority", "workdir"}


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
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class DispatchResult:
    started: tuple[str, ...]
    skipped_reason: str = ""


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
    formatter = string.Formatter()
    for _, field_name, _, _ in formatter.parse(part):
        if field_name is None:
            continue
        if "." in field_name or "[" in field_name or "]" in field_name:
            raise ValueError(f"unsupported command placeholder: {field_name}")
        if field_name not in _ALLOWED_COMMAND_FIELDS:
            raise ValueError(f"unsupported command placeholder: {field_name}")
    return part.format_map(fields)


def render_command_argv(template: str, task: TaskRecord) -> tuple[str, ...]:
    """Render a worker command template to subprocess argv.

    The template is split with shell-style quoting before task placeholders are
    substituted. That keeps task metadata as data: a placeholder value containing
    spaces, quotes, or punctuation cannot create extra argv tokens.
    """

    fields = _command_fields(task)
    return tuple(_render_template_part(part, fields) for part in shlex.split(template))


def render_command_template(template: str, task: TaskRecord) -> str:
    """Render a worker command template for display or event logging.

    Supported placeholders are ``{task_id}``, ``{title}``, ``{lane}``, ``{role}``,
    ``{priority}``, and ``{workdir}``. Unknown fields fail before a subprocess is
    started so typos do not turn into surprising shell arguments.
    """

    return shlex.join(render_command_argv(template, task))


def _command_argv_for_task(task: TaskRecord, policy: DispatchPolicy) -> tuple[str, ...]:
    template = policy.command or task.command
    if not template:
        raise ValueError("subprocess backend requires a policy command or per-task command")
    return render_command_argv(template, task)


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
        argv = _command_argv_for_task(task, policy)
        store.mark_task(task.id, TaskStatus.RUNNING, f"started: {shlex.join(argv)}")
        completed = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=policy.timeout_seconds,
            cwd=Path(task.workdir) if task.workdir else None,
        )
        if completed.returncode == 0:
            store.mark_task(task.id, TaskStatus.DONE, completed.stdout[-500:])
        else:
            store.mark_task(
                task.id, TaskStatus.FAILED, (completed.stderr or completed.stdout)[-500:]
            )
        started.append(task.id)
    return DispatchResult(tuple(started))
