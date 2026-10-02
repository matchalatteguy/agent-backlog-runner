"""Idempotent manifests for finite repository-maintenance batches."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .models import TaskRecord
from .safety import require_safe_slug, safe_child_path
from .store import TaskStore
from .templates import _load_data


@dataclass(frozen=True)
class BatchTask:
    slug: str
    title: str
    argv: tuple[str, ...]
    workdir: str
    timeout_seconds: float
    max_attempts: int
    retry_backoff_seconds: float


@dataclass(frozen=True)
class BatchManifest:
    name: str
    root: str
    tasks: tuple[BatchTask, ...]


def load_batch_manifest(
    path: str | Path, *, root: str | Path | None = None, python: str | None = None
) -> BatchManifest:
    path = Path(path)
    data = _load_data(path)
    if not isinstance(data, dict) or set(data) - {"schema_version", "name", "tasks"}:
        raise ValueError("batch must be an object with schema_version, name, and tasks only")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported batch schema_version; expected 1")
    name = require_safe_slug(data.get("name", ""), field="batch name")
    directory = Path(root or path.parent).resolve()
    if not directory.is_dir():
        raise ValueError("batch root must be an existing directory")
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("batch tasks must be a non-empty list")
    tasks = []
    allowed = {
        "id",
        "title",
        "argv",
        "workdir",
        "timeout_seconds",
        "max_attempts",
        "retry_backoff_seconds",
    }
    for raw in raw_tasks:
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("unknown or malformed batch task fields")
        slug = require_safe_slug(raw.get("id", ""), field="batch task id")
        title = raw.get("title", slug)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("batch title must be a non-empty string")
        argv = raw.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(arg, str) and "\0" not in arg for arg in argv)
            or not argv[0]
        ):
            raise ValueError("argv must be a non-empty list of string arguments without NUL bytes")
        argv = tuple((python or sys.executable) if arg == "{python}" else arg for arg in argv)
        workdir = raw.get("workdir", ".")
        if not isinstance(workdir, str) or not workdir:
            raise ValueError("workdir must be a non-empty relative string")
        resolved = safe_child_path(directory, workdir)
        if not resolved.is_dir():
            raise ValueError(f"workdir does not exist: {workdir}")
        timeout, budget, backoff = (
            raw.get("timeout_seconds", 300),
            raw.get("max_attempts", 3),
            raw.get("retry_backoff_seconds", 1),
        )
        if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
            raise ValueError("timeout_seconds must be positive and at most 86400")
        if type(budget) is not int or not 1 <= budget <= 100:
            raise ValueError("max_attempts must be an integer from 1 to 100")
        if type(backoff) not in (int, float) or not 0 <= backoff <= 60:
            raise ValueError("retry_backoff_seconds must be between 0 and 60")
        tasks.append(BatchTask(slug, title, argv, str(resolved), timeout, budget, backoff))
    if len({task.slug for task in tasks}) != len(tasks):
        raise ValueError("batch task ids must be unique")
    return BatchManifest(name, str(directory), tuple(tasks))


def enqueue_batch(
    store: TaskStore, manifest: BatchManifest, *, run_id: str = "default"
) -> tuple[TaskRecord, ...]:
    run_id = require_safe_slug(run_id, field="run id")
    records = []
    with store.transaction():
        for item in manifest.tasks:
            identity = json.dumps([manifest.root, manifest.name, run_id, item.slug])
            task_id = "task_" + hashlib.sha256(identity.encode()).hexdigest()[:12]
            body = f"batch={manifest.name}; run={run_id}; check={item.slug}"
            existing = store.get_task(task_id)
            if existing:
                expected = (
                    item.title,
                    item.argv,
                    item.workdir,
                    item.timeout_seconds,
                    item.max_attempts,
                    item.retry_backoff_seconds,
                )
                actual = (
                    existing.title,
                    existing.command_argv,
                    existing.workdir,
                    existing.timeout_seconds,
                    existing.max_attempts,
                    existing.retry_backoff_seconds,
                )
                if expected != actual or existing.body != body:
                    raise ValueError(
                        "batch definition changed for an existing run; use a new --run-id"
                    )
                records.append(existing)
                continue
            records.append(
                store.create_task(
                    task_id=task_id,
                    title=item.title,
                    body=body,
                    lane="checks",
                    tags=(manifest.name,),
                    role="worker",
                    command_argv=item.argv,
                    workdir=item.workdir,
                    timeout_seconds=item.timeout_seconds,
                    max_attempts=item.max_attempts,
                    retry_backoff_seconds=item.retry_backoff_seconds,
                )
            )
    return tuple(records)
