from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .models import TaskEvent, TaskRecord, TaskStatus
from .safety import require_safe_slug, require_safe_task_id

SCHEMA_VERSION = 1

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
"""


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
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def __enter__(self) -> TaskStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA)
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
    ) -> TaskRecord:
        task_id = require_safe_task_id(task_id or self.new_task_id())
        lane = require_safe_slug(lane, field="lane")
        role = require_safe_slug(role, field="role")
        for tag in tags:
            require_safe_slug(tag, field="tag")
        now = _now()
        with self.conn:
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
        with self.conn:
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
                "ORDER BY priority DESC, created_at ASC"
            )
            rows = self.conn.execute(
                query,
                tuple(status.value for status in statuses),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tasks ORDER BY priority DESC, created_at ASC"
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def mark_task(self, task_id: str, status: TaskStatus, message: str = "") -> None:
        require_safe_task_id(task_id)
        event_type = {
            TaskStatus.RUNNING: "dispatched",
            TaskStatus.BLOCKED: "blocked",
            TaskStatus.DONE: "completed",
            TaskStatus.FAILED: "failed",
            TaskStatus.CANCELLED: "cancelled",
            TaskStatus.TODO: "queued",
        }[status]
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status.value, _now(), task_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(task_id)
            self._add_event_unlocked(task_id, event_type, message, {})

    def record_heartbeat(self, task_id: str, payload: dict[str, Any] | None = None) -> None:
        require_safe_task_id(task_id)
        now = _now()
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE tasks SET heartbeat_at=?, updated_at=? WHERE id=?",
                (now, now, task_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(task_id)
            self._add_event_unlocked(task_id, "heartbeat", "heartbeat", payload or {})

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
        with self.conn:
            self.conn.execute(
                """INSERT INTO scheduler_state(key,value,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at""",
                (key, str(value), _now()),
            )


def init_store(path: str | Path) -> TaskStore:
    store = TaskStore(path)
    store.init_schema()
    return store


def mark_task(store: TaskStore, task_id: str, status: TaskStatus, message: str = "") -> None:
    store.mark_task(task_id, status, message)


def record_heartbeat(store: TaskStore, task_id: str, payload: dict[str, Any] | None = None) -> None:
    store.record_heartbeat(task_id, payload)
