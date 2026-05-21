# Agent Backlog Runner

Agent Backlog Runner is a small local control plane for agent task queues. It keeps a SQLite backlog healthy from validated templates, previews replenishment plans, dispatches a bounded number of harmless local worker commands, and reports ready, running, blocked, stale, and finished tasks.

It is intentionally boring: local files, no hosted service, no credentials, and no network integration.

## What it does

- Stores tasks and event history in SQLite.
- Loads JSON or YAML task templates with strict validation.
- Plans queue-depth replenishment with `min_queue_depth`, `target_queue_depth`, and `max_enqueue_per_cycle`.
- Supports dry-run planning with no task creation or subprocesses.
- Caps local dispatch concurrency.
- Records heartbeats and reports stale running tasks.
- Provides both a CLI and a small typed Python API.

## Install for development

```bash
uv sync --extra dev
uv run agent-backlog --help
```

## Quickstart

```bash
uv run agent-backlog init --db .agent-backlog/tasks.sqlite3
uv run agent-backlog templates validate --templates examples/templates/basic-backlog.yaml
uv run agent-backlog scheduler plan --db .agent-backlog/tasks.sqlite3 --templates examples/templates/basic-backlog.yaml --min-depth 2 --target-depth 4 --dry-run --json
uv run agent-backlog scheduler run --db .agent-backlog/tasks.sqlite3 --templates examples/templates/basic-backlog.yaml --min-depth 2 --target-depth 4
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
```

Dispatch is opt-in. Start with dry-run dispatch previews:

```bash
uv run agent-backlog dispatch --db .agent-backlog/tasks.sqlite3 --max-workers 2
```

Then try the example worker command:

```bash
uv run agent-backlog dispatch --db .agent-backlog/tasks.sqlite3 --max-workers 1 --backend subprocess --command "bash examples/worker_echo.sh {task_id}"
```

## Template example

```yaml
templates:
  - slug: docs-refresh
    title: "Refresh docs section ${sequence}"
    body: "Review one documentation page and list confusing steps."
    role: writer
    lane: docs
    priority: 10
    tags: [docs, quality]
    acceptance:
      - "Notes are clear and actionable."
```

## Safety model

Agent Backlog Runner is local-first and fail-closed. It rejects unsafe identifiers, validates template fields, performs dry runs without writes, and never removes directories as part of normal operation. Worker dispatch is disabled unless explicitly requested.

## Non-goals

This project is not a hosted project-management app, a distributed workflow engine, a CI system, or a replacement for a full orchestration platform.
