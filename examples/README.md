# Examples

The examples are synthetic and safe to run locally. They are designed to show queue mechanics, not domain-specific automation.

## Files

- `templates/basic-backlog.yaml`: three task templates for documentation refresh, test hardening, and dependency notes.
- `worker_echo.sh`: harmless local demo worker. It prints the task id and exits successfully.

## Validate the catalog

```bash
uv run agent-backlog templates validate --templates examples/templates/basic-backlog.yaml
```

Expected output:

```text
valid templates: 3
```

## Create demo tasks

```bash
uv run agent-backlog init --db .agent-backlog/tasks.sqlite3
uv run agent-backlog scheduler run \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 \
  --target-depth 4 \
  --max-enqueue 3
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
```

## Try dispatch

Preview only:

```bash
uv run agent-backlog dispatch --db .agent-backlog/tasks.sqlite3 --max-workers 2
```

Run one harmless worker:

```bash
uv run agent-backlog dispatch \
  --db .agent-backlog/tasks.sqlite3 \
  --max-workers 1 \
  --backend subprocess \
  --command "bash examples/worker_echo.sh {task_id}"
```

Then inspect events:

```bash
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 20
```

## Modify safely

To adapt these examples, keep templates small and generic. Good starter lanes are `docs`, `quality`, and `maintenance`. Good starter tasks are checklists, notes, and deterministic local scripts.
