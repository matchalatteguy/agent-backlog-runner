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

## Events

Every important state change writes an event row. Recent events are useful when a scheduler run or dispatch cycle did something unexpected.

```bash
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 20
```

Typical event types include:

- `created`
- `dispatch_preview`
- `status_changed`
- `heartbeat`

## Heartbeats

Library callers can refresh a running task heartbeat:

```python
from agent_backlog_runner import init_store

store = init_store(".agent-backlog/tasks.sqlite3")
store.record_heartbeat("task_000001", {"step": "checking-docs"})
store.close()
```

A heartbeat updates the task and records an event payload.

## Stale tasks

A running task is stale when its latest heartbeat or status update is older than the configured threshold.

```bash
uv run agent-backlog stale --db .agent-backlog/tasks.sqlite3 --after 30m
```

The command prints stale task ids and their age in seconds. It does not change task status; callers decide whether to retry, mark failed, or investigate.
