# Status and heartbeats

Status commands summarize what is in the local SQLite store: task counts, running work, stale tasks, and recent events.

## Status table

```bash
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
```

The table output is meant for quick terminal checks. Use JSON when another script needs to read the snapshot:

```bash
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3 --format json
```

## Task listing and detail

Use `list` for a queue-level task table and `show` for one task plus recent event history:

```bash
uv run agent-backlog list --db .agent-backlog/tasks.sqlite3 --status todo
uv run agent-backlog show --db .agent-backlog/tasks.sqlite3 task_000001 --format json
```

`--format json` is stable enough for local scripts and contains the task fields stored in SQLite.

## Lifecycle updates

Use `mark` for explicit human- or script-driven state changes, and `heartbeat` for progress pings from running work:

```bash
uv run agent-backlog mark --db .agent-backlog/tasks.sqlite3 task_000001 --status running --message "claimed by local worker"
uv run agent-backlog heartbeat --db .agent-backlog/tasks.sqlite3 task_000001 --payload '{"step":"checking-docs"}'
```

Allowed statuses are `todo`, `running`, `blocked`, `done`, `failed`, and `cancelled`.

## Events

Every important state change writes an event row. Recent events are useful when a scheduler run or dispatch cycle did something unexpected.

```bash
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 20
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 20 --format json
```

Typical event types include:

- `created`
- `dispatch_preview`
- `dispatched`
- `blocked`
- `queued`
- `completed`
- `failed`
- `cancelled`
- `heartbeat`

## Heartbeats

CLI users can run `agent-backlog heartbeat`; library callers can use the same operation directly:

```python
from agent_backlog_runner import init_store

with init_store(".agent-backlog/tasks.sqlite3") as store:
    store.record_heartbeat("task_000001", {"step": "checking-docs"})
```

A heartbeat updates the task and records an event payload.

## Stale tasks

A running task is stale when its latest heartbeat or status update is older than the configured threshold.

```bash
uv run agent-backlog stale --db .agent-backlog/tasks.sqlite3 --after 30m
uv run agent-backlog stale --db .agent-backlog/tasks.sqlite3 --after 30m --format json
```

The command prints stale task ids and their age in seconds. It does not change task status; callers decide whether to retry, mark failed, or investigate.
