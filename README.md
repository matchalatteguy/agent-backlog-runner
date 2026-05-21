# Agent Backlog Runner

Agent Backlog Runner is a small local control plane for agent task queues. It keeps a SQLite backlog healthy from validated templates, previews replenishment plans, dispatches a bounded number of harmless local worker commands, and reports ready, running, blocked, stale, and finished tasks.

It is intentionally boring: local files, no hosted service, no credentials, and no network integration.

## Why this exists

Local agent experiments often start as a text file plus a shell loop. That works until the queue runs dry, too many workers start at once, or a stale task sits unnoticed. Agent Backlog Runner adds just enough durable state to answer four questions:

1. Do we have enough ready work?
2. Which templates would create more work, and would that be safe?
3. How many local workers may start right now?
4. Which tasks are running, blocked, done, failed, or stale?

## What it does

- Stores tasks and event history in SQLite.
- Loads JSON or YAML task templates with strict validation.
- Plans queue-depth replenishment with `min_queue_depth`, `target_queue_depth`, and `max_enqueue_per_cycle`.
- Supports dry-run planning with no task creation or subprocesses.
- Caps local dispatch concurrency.
- Records heartbeats and reports stale running tasks.
- Provides both a CLI and a small typed Python API.

```text
synthetic templates -> validate -> plan -> run -> dispatch -> status/events/stale
                         |         |
                         |         +-- dry-run previews before writes
                         +-- fail-closed schema checks
```

## Install for development

Requirements: Python 3.11+ and `uv`.

```bash
uv sync --extra dev
uv run agent-backlog --help
```

## First 5 minutes

Run the full local demo from a fresh checkout. The demo creates only ignored files under `.agent-backlog/`.

```bash
# 1. Create a local SQLite task store.
uv run agent-backlog init --db .agent-backlog/tasks.sqlite3

# 2. Validate the synthetic template catalog.
uv run agent-backlog templates validate --templates examples/templates/basic-backlog.yaml

# 3. Preview replenishment. This creates no tasks.
uv run agent-backlog scheduler plan \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 \
  --target-depth 4 \
  --max-enqueue 3 \
  --dry-run \
  --json

# 4. Apply the plan and create ready tasks.
uv run agent-backlog scheduler run \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 \
  --target-depth 4 \
  --max-enqueue 3

# 5. Inspect the queue.
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 10
```

Expected shape:

```text
initialized .agent-backlog/tasks.sqlite3
valid templates: 3
created 3
status    count
-------   -----
todo      3
```

## Dispatch a harmless worker

Dispatch is opt-in. Start with the default dry-run backend; it records preview events and does not run a subprocess.

```bash
uv run agent-backlog dispatch --db .agent-backlog/tasks.sqlite3 --max-workers 2
```

Then run the example worker command. It prints the task id and exits successfully.

```bash
uv run agent-backlog dispatch \
  --db .agent-backlog/tasks.sqlite3 \
  --max-workers 1 \
  --backend subprocess \
  --command "bash examples/worker_echo.sh {task_id}"

uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 20
```

## Template example

```yaml
templates:
  - slug: docs-refresh
    title: "Refresh docs section ${sequence}"
    body: "Review one documentation page and write a short improvement note."
    role: writer
    lane: docs
    priority: 20
    tags: [docs, quality]
    acceptance:
      - "The note names the page and the suggested edit."
```

Use `--lane docs` or `--tag quality` on scheduler commands to narrow which templates are eligible.

## CLI map

```text
agent-backlog init --db PATH
agent-backlog templates validate --templates PATH
agent-backlog scheduler plan --db PATH --templates PATH [--dry-run] [--json]
agent-backlog scheduler run --db PATH --templates PATH
agent-backlog enqueue --db PATH --title TEXT [--body TEXT | --body-file PATH]
agent-backlog dispatch --db PATH [--backend dry-run|subprocess] [--command CMD]
agent-backlog status --db PATH [--format table|json]
agent-backlog events --db PATH [--limit N]
agent-backlog stale --db PATH [--after 30m]
```

## Python API sketch

```python
from agent_backlog_runner import BacklogPolicy, init_store, load_template_catalog
from agent_backlog_runner.scheduler import apply_backlog_plan, plan_backlog

store = init_store(".agent-backlog/tasks.sqlite3")
catalog = load_template_catalog("examples/templates/basic-backlog.yaml")
plan = plan_backlog(store, catalog, BacklogPolicy(min_queue_depth=2, target_queue_depth=4))
created = apply_backlog_plan(store, plan)
print([task.id for task in created])
store.close()
```

## Docs

- `docs/first-5-minutes.md`: copy/paste walkthrough with restart notes.
- `docs/template-schema.md`: YAML/JSON template fields and validation rules.
- `docs/scheduler-policy.md`: queue-depth planning and dry-run behavior.
- `docs/dispatch-backends.md`: dry-run and subprocess dispatch boundaries.
- `docs/status-and-heartbeats.md`: status counts, events, stale checks, and heartbeat API.
- `docs/safety-model.md`: local-first side-effect boundaries.

## Safety model

Agent Backlog Runner is local-first and fail-closed. It rejects unsafe identifiers, validates template fields, performs dry runs without writes, and never removes directories as part of normal operation. Worker dispatch is disabled unless explicitly requested.

## Non-goals

This project is not a hosted project-management app, a distributed workflow engine, a CI system, or a replacement for a full orchestration platform.
