from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import TaskEvent, TaskRecord, TaskStatus
from .safety import require_safe_slug, require_safe_task_id

SCHEMA_VERSION = 2

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  lane TEXT NOT NULL DEFAULT 'default',
  tags TEXT NOT NULL DEFAULT '[]',
  role TEXT NOT NULL DEFAULT 'agent',
  workdir TEXT,
  command TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  heartbeat_at INTEGER
);
CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  event_type TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduler_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_priority
ON tasks(status, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_events_task_id ON task_events(task_id, id DESC);
CREATE TABLE IF NOT EXISTS task_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  attempt_no INTEGER NOT NULL,
  claim_token TEXT NOT NULL UNIQUE,
  owner_pid INTEGER NOT NULL,
  worker_pid INTEGER,
  process_group INTEGER,
  argv TEXT NOT NULL,
  workdir TEXT,
  started_at REAL NOT NULL,
  finished_at REAL,
  outcome TEXT NOT NULL DEFAULT 'running',
  exit_code INTEGER,
  stdout_path TEXT,
  stderr_path TEXT,
  stdout_bytes INTEGER NOT NULL DEFAULT 0,
  stderr_bytes INTEGER NOT NULL DEFAULT 0,
  stdout_truncated INTEGER NOT NULL DEFAULT 0,
  stderr_truncated INTEGER NOT NULL DEFAULT 0,
  UNIQUE(task_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS idx_attempts_task ON task_attempts(task_id, attempt_no);
"""

_TASK_COLUMNS = {
    "command_argv": "TEXT",
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
    "max_attempts": "INTEGER NOT NULL DEFAULT 3",
    "retry_backoff_seconds": "REAL NOT NULL DEFAULT 1",
    "timeout_seconds": "REAL",
    "not_before": "REAL NOT NULL DEFAULT 0",
}


def _now() -> int:
    return int(time.time())


def _task_from_row(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        id=row["id"],
        title=row["title"],
        body=row["body"],
        status=TaskStatus(row["status"]),
        priority=row["priority"],
        lane=row["lane"],
        tags=tuple(json.loads(row["tags"])),
        role=row["role"],
        workdir=row["workdir"],
        command=row["command"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        heartbeat_at=row["heartbeat_at"],
        command_argv=tuple(json.loads(row["command_argv"])) if row["command_argv"] else None,
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        retry_backoff_seconds=row["retry_backoff_seconds"],
        timeout_seconds=row["timeout_seconds"],
        not_before=row["not_before"],
    )


def _event_from_row(row: sqlite3.Row) -> TaskEvent:
    return TaskEvent(
        id=row["id"],
        task_id=row["task_id"],
        event_type=row["event_type"],
        message=row["message"],
        payload=json.loads(row["payload"]),
        created_at=row["created_at"],
    )


class TaskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.exists() and self.path.is_dir():
            raise ValueError("database path is a directory")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def __enter__(self) -> TaskStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
        ).fetchone()
        if exists and self.schema_version() > SCHEMA_VERSION:
            raise ValueError("database schema is newer than this package supports")
        with self.conn:
            self.conn.executescript(SCHEMA)
        with self.transaction():
            columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(tasks)")}
            for name, definition in _TASK_COLUMNS.items():
                if name not in columns:
                    self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            self.conn.execute(
                """INSERT INTO schema_meta(key,value,updated_at) VALUES ('schema_version',?,?)
                ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at""",
                (str(SCHEMA_VERSION), _now()),
            )

    def schema_version(self) -> int:
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            return 0
        return int(row["value"])

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """An atomic write boundary shared by task, event, and scheduler writes."""
        if self.conn.in_transaction:
            yield
            return
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            yield

    def new_task_id(self) -> str:
        while True:
            task_id = "task_" + uuid.uuid4().hex[:12]
            if self.get_task(task_id) is None:
                return task_id

    def create_task(
        self,
        *,
        title: str,
        body: str,
        priority: int = 0,
        lane: str = "default",
        tags: tuple[str, ...] = (),
        role: str = "agent",
        task_id: str | None = None,
        command: str | None = None,
        workdir: str | None = None,
        event_message: str = "created",
        command_argv: tuple[str, ...] | None = None,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1,
        timeout_seconds: float | None = None,
    ) -> TaskRecord:
        task_id = require_safe_task_id(task_id or self.new_task_id())
        lane = require_safe_slug(lane, field="lane")
        role = require_safe_slug(role, field="role")
        for tag in tags:
            require_safe_slug(tag, field="tag")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValueError("max_attempts must be an integer from 1 to 100")
        if type(retry_backoff_seconds) not in (int, float) or not 0 <= retry_backoff_seconds <= 60:
            raise ValueError("retry_backoff_seconds must be between 0 and 60")
        if timeout_seconds is not None and (
            type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 86400
        ):
            raise ValueError("timeout_seconds must be positive and at most 86400")
        if command_argv is not None and (
            not command_argv
            or not all(isinstance(arg, str) and "\0" not in arg for arg in command_argv)
        ):
            raise ValueError("command_argv must contain string arguments without NUL bytes")
        now = _now()
        with self.transaction():
            self.conn.execute(
                """INSERT INTO tasks
                (id,title,body,status,priority,lane,tags,role,workdir,command,created_at,updated_at,heartbeat_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    title,
                    body,
                    TaskStatus.TODO.value,
                    int(priority),
                    lane,
                    json.dumps(list(tags)),
                    role,
                    workdir,
                    command,
                    now,
                    now,
                    None,
                ),
            )
            self._add_event_unlocked(task_id, "created", event_message, {})
            self.conn.execute(
                """UPDATE tasks SET command_argv=?,max_attempts=?,retry_backoff_seconds=?,
                timeout_seconds=? WHERE id=?""",
                (
                    json.dumps(command_argv) if command_argv else None,
                    max_attempts,
                    retry_backoff_seconds,
                    timeout_seconds,
                    task_id,
                ),
            )
        task = self.get_task(task_id)
        assert task is not None
        return task

    def _add_event_unlocked(
        self,
        task_id: str,
        event_type: str,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO task_events(task_id,event_type,message,payload,created_at)
            VALUES (?,?,?,?,?)""",
            (task_id, event_type, message, json.dumps(payload or {}, sort_keys=True), _now()),
        )

    def add_event(
        self,
        task_id: str,
        event_type: str,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        require_safe_task_id(task_id)
        with self.transaction():
            self._add_event_unlocked(task_id, event_type, message, payload)

    def get_task(self, task_id: str) -> TaskRecord | None:
        require_safe_task_id(task_id)
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return None if row is None else _task_from_row(row)

    def list_tasks(self, statuses: tuple[TaskStatus, ...] = ()) -> list[TaskRecord]:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query = (
                f"SELECT * FROM tasks WHERE status IN ({placeholders}) "
                "ORDER BY priority DESC, created_at ASC, rowid ASC"
            )
            rows = self.conn.execute(
                query,
                tuple(status.value for status in statuses),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tasks ORDER BY priority DESC, created_at ASC, rowid ASC"
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def mark_task(
        self,
        task_id: str,
        status: TaskStatus,
        message: str = "",
        *,
        expected_status: TaskStatus | None = None,
    ) -> bool:
        require_safe_task_id(task_id)
        event_type = {
            TaskStatus.RUNNING: "dispatched",
            TaskStatus.BLOCKED: "blocked",
            TaskStatus.DONE: "completed",
            TaskStatus.FAILED: "failed",
            TaskStatus.CANCELLED: "cancelled",
            TaskStatus.TODO: "queued",
        }[status]
        with self.transaction():
            task = self.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            if expected_status is not None and task.status != expected_status:
                return False
            cursor = self.conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status.value, _now(), task_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(task_id)
            self._add_event_unlocked(task_id, event_type, message, {})
        return True

    def claim_task(self, task_id: str, max_running: int, message: str) -> bool:
        """Claim a TODO task and a capacity slot before executing its command."""
        require_safe_task_id(task_id)
        with self.transaction():
            task = self.get_task(task_id)
            if task is None or task.status != TaskStatus.TODO:
                return False
            running = self.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status=?",
                (TaskStatus.RUNNING.value,),
            ).fetchone()[0]
            if running >= max_running:
                return False
            now = _now()
            self.conn.execute(
                "UPDATE tasks SET status=?, updated_at=?, heartbeat_at=? WHERE id=?",
                (TaskStatus.RUNNING.value, now, now, task_id),
            )
            self._add_event_unlocked(task_id, "dispatched", message, {})
        return True

    def record_heartbeat(self, task_id: str, payload: dict[str, Any] | None = None) -> None:
        require_safe_task_id(task_id)
        now = _now()
        with self.transaction():
            cursor = self.conn.execute(
                "UPDATE tasks SET heartbeat_at=?, updated_at=? WHERE id=?",
                (now, now, task_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(task_id)
            self._add_event_unlocked(task_id, "heartbeat", "heartbeat", payload or {})

    def attempts(self, task_id: str | None = None) -> list[dict[str, Any]]:
        if task_id is not None:
            require_safe_task_id(task_id)
        rows = self.conn.execute(
            "SELECT * FROM task_attempts"
            + (" WHERE task_id=?" if task_id else "")
            + " ORDER BY id DESC",
            (task_id,) if task_id else (),
        ).fetchall()
        return [{**dict(row), "argv": json.loads(row["argv"])} for row in rows]

    def attempt(self, token: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM task_attempts WHERE claim_token=?", (token,)
        ).fetchone()
        return None if row is None else {**dict(row), "argv": json.loads(row["argv"])}

    def claim_attempt(
        self, task_id: str, max_running: int, argv: tuple[str, ...]
    ) -> dict[str, Any] | None:
        """Reserve capacity and a unique attempt in one write transaction."""
        require_safe_task_id(task_id)
        with self.transaction():
            task = self.get_task(task_id)
            if (
                task is None
                or task.status != TaskStatus.TODO
                or task.not_before > time.time()
                or task.attempt_count >= task.max_attempts
            ):
                return None
            if self.conn.execute(
                "SELECT 1 FROM task_attempts WHERE task_id=? AND finished_at IS NULL", (task_id,)
            ).fetchone():
                return None
            # Cancelled but not yet stopped attempts still consume physical capacity.
            running = self.conn.execute("""SELECT
                (SELECT COUNT(*) FROM task_attempts WHERE finished_at IS NULL) +
                (SELECT COUNT(*) FROM tasks t WHERE status='running' AND NOT EXISTS
                 (SELECT 1 FROM task_attempts a WHERE a.task_id=t.id AND a.finished_at IS NULL))
                """).fetchone()[0]
            capacity = self.conn.execute(
                "SELECT value FROM schema_meta WHERE key='dispatch_capacity'"
            ).fetchone()
            if running and capacity and int(capacity[0]) != max_running:
                raise ValueError("active dispatchers must use the same --max-workers capacity")
            if not running or capacity is None:
                self.conn.execute(
                    """INSERT INTO schema_meta(key,value,updated_at) VALUES('dispatch_capacity',?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                    updated_at=excluded.updated_at""",
                    (str(max_running), _now()),
                )
            if running >= max_running:
                return None
            token = uuid.uuid4().hex
            now = time.time()
            self.conn.execute(
                """INSERT INTO task_attempts
                (task_id,attempt_no,claim_token,owner_pid,argv,workdir,started_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    task_id,
                    task.attempt_count + 1,
                    token,
                    os.getpid(),
                    json.dumps(argv),
                    task.workdir,
                    now,
                ),
            )
            self.conn.execute(
                """UPDATE tasks SET status='running',attempt_count=attempt_count+1,
                updated_at=?,heartbeat_at=? WHERE id=?""",
                (int(now), int(now), task_id),
            )
            self._add_event_unlocked(
                task_id, "dispatched", "worker attempt claimed", {"claim_token": token}
            )
        return self.attempt(token)

    def bind_attempt(self, token: str, **values: Any) -> None:
        allowed = {"worker_pid", "process_group", "stdout_path", "stderr_path"}
        if not values or set(values) - allowed:
            raise ValueError("invalid attempt binding fields")
        with self.transaction():
            cursor = self.conn.execute(
                "UPDATE task_attempts SET "
                + ",".join(f"{key}=?" for key in values)
                + " WHERE claim_token=? AND finished_at IS NULL",
                (*values.values(), token),
            )
            if cursor.rowcount != 1:
                raise ValueError("attempt is no longer active")

    def heartbeat_attempt(self, token: str) -> None:
        with self.transaction():
            self.conn.execute(
                """UPDATE tasks SET heartbeat_at=?,updated_at=?
                WHERE status='running' AND id=(SELECT task_id FROM task_attempts
                WHERE claim_token=? AND finished_at IS NULL)""",
                (_now(), _now(), token),
            )

    def finish_attempt(
        self,
        token: str,
        outcome: str,
        message: str,
        *,
        exit_code: int | None = None,
        stdout_bytes: int = 0,
        stderr_bytes: int = 0,
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
    ) -> bool:
        if outcome not in {
            "done",
            "failed",
            "timeout",
            "cancelled",
            "interrupted",
            "abandoned",
            "spawn_error",
        }:
            raise ValueError("invalid attempt outcome")
        with self.transaction():
            attempt = self.attempt(token)
            if attempt is None or attempt["finished_at"] is not None:
                return False
            self.conn.execute(
                """UPDATE task_attempts SET outcome=?,finished_at=?,exit_code=?,
                stdout_bytes=?,stderr_bytes=?,stdout_truncated=?,stderr_truncated=?
                WHERE claim_token=?""",
                (
                    outcome,
                    time.time(),
                    exit_code,
                    stdout_bytes,
                    stderr_bytes,
                    int(stdout_truncated),
                    int(stderr_truncated),
                    token,
                ),
            )
            status = (
                TaskStatus.DONE
                if outcome == "done"
                else (TaskStatus.CANCELLED if outcome == "cancelled" else TaskStatus.FAILED)
            )
            task = self.get_task(attempt["task_id"])
            if task and task.status == TaskStatus.RUNNING:
                self.conn.execute(
                    "UPDATE tasks SET status=?,updated_at=? WHERE id=?",
                    (status.value, _now(), task.id),
                )
            event_type = (
                "completed"
                if outcome == "done"
                else ("cancelled" if outcome == "cancelled" else "failed")
            )
            self._add_event_unlocked(
                attempt["task_id"],
                event_type,
                message,
                {
                    "attempt": attempt["attempt_no"],
                    "outcome": outcome,
                    "exit_code": exit_code,
                    "stdout_path": attempt["stdout_path"],
                    "stderr_path": attempt["stderr_path"],
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                },
            )
        return True

    def retry_task(self, task_id: str) -> TaskRecord:
        """Explicitly schedule one more attempt; never retry as a side effect of dispatch."""
        with self.transaction():
            task = self.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                raise ValueError("only failed or cancelled tasks can be retried")
            if any(attempt["finished_at"] is None for attempt in self.attempts(task_id)):
                raise ValueError(
                    "attempt is still active; wait for cancellation or recover it first"
                )
            if task.attempt_count >= task.max_attempts:
                raise ValueError("attempt budget exhausted")
            delay = min(60, task.retry_backoff_seconds * 2 ** max(0, task.attempt_count - 1))
            due = time.time() + delay
            self.conn.execute(
                "UPDATE tasks SET status='todo',not_before=?,updated_at=? WHERE id=?",
                (due, _now(), task_id),
            )
            self._add_event_unlocked(
                task_id,
                "retry_scheduled",
                f"retry available after {delay:g}s",
                {"not_before": due, "next_attempt": task.attempt_count + 1},
            )
        result = self.get_task(task_id)
        assert result is not None
        return result

    def request_cancel(
        self, task_id: str, message: str = "operator requested cancellation"
    ) -> None:
        with self.transaction():
            task = self.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status not in {TaskStatus.TODO, TaskStatus.RUNNING, TaskStatus.BLOCKED}:
                raise ValueError("task is already terminal")
            if task.status == TaskStatus.RUNNING and not any(
                attempt["finished_at"] is None for attempt in self.attempts(task_id)
            ):
                raise ValueError(
                    "running task has no managed attempt; inspect and recover it first"
                )
            self.mark_task(task_id, TaskStatus.CANCELLED, message)

    def active_depth(self, lanes: tuple[str, ...] = ()) -> int:
        params: list[Any] = [TaskStatus.TODO.value, TaskStatus.RUNNING.value]
        where = "status IN (?,?)"
        if lanes:
            where += " AND lane IN (" + ",".join("?" for _ in lanes) + ")"
            params.extend(lanes)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS count FROM tasks WHERE {where}", params
        ).fetchone()
        return int(row["count"])

    def title_exists(self, title: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM tasks WHERE title=? LIMIT 1", (title,)).fetchone()
        return row is not None

    def events(self, task_id: str | None = None, limit: int = 20) -> list[TaskEvent]:
        if task_id is None:
            rows = self.conn.execute(
                "SELECT * FROM task_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            require_safe_task_id(task_id)
            rows = self.conn.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def get_state(self, key: str, default: int = 0) -> int:
        require_safe_slug(key, field="state key")
        row = self.conn.execute("SELECT value FROM scheduler_state WHERE key=?", (key,)).fetchone()
        return default if row is None else int(row["value"])

    def set_state(self, key: str, value: int) -> None:
        require_safe_slug(key, field="state key")
        with self.transaction():
            self.conn.execute(
                """INSERT INTO scheduler_state(key,value,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at""",
                (key, str(value), _now()),
            )


def init_store(path: str | Path) -> TaskStore:
    store = TaskStore(path)
    try:
        store.init_schema()
    except Exception:
        store.close()
        raise
    return store


def mark_task(store: TaskStore, task_id: str, status: TaskStatus, message: str = "") -> None:
    store.mark_task(task_id, status, message)


def record_heartbeat(store: TaskStore, task_id: str, payload: dict[str, Any] | None = None) -> None:
    store.record_heartbeat(task_id, payload)
