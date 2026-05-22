# First 5 minutes

This walkthrough proves the core loop without credentials, network calls, or long-running services. It uses the synthetic templates in `examples/templates/basic-backlog.yaml` and writes demo state under `.agent-backlog/`, which is ignored by version control.

The goal is to see the reusable abstraction in action:

```text
validated templates -> dry-run plan -> SQLite tasks -> optional local worker -> status/events
```

## 0. Install developer dependencies

```bash
uv sync --extra dev
uv run agent-backlog --help
```

If `uv` is unavailable, see `docs/troubleshooting.md`.

## 1. Initialize a local task store

```bash
uv run agent-backlog init --db .agent-backlog/tasks.sqlite3
```

Expected output:

```text
initialized .agent-backlog/tasks.sqlite3
```

You can omit `--db` when `AGENT_BACKLOG_DB` is set or when you want the default `.agent-backlog/tasks.sqlite3` in the current directory.

## 2. Validate the template catalog

```bash
uv run agent-backlog templates validate --templates examples/templates/basic-backlog.yaml
```

Expected output:

```text
valid templates: 3
```

If validation fails, the CLI exits non-zero instead of silently using a malformed catalog.

## 3. Preview queue replenishment

```bash
uv run agent-backlog scheduler plan \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 \
  --target-depth 4 \
  --max-enqueue 3 \
  --dry-run \
  --json
```

A plan preview should list up to three task cards. It does not create task rows, files, directories, or subprocesses.

## 4. Apply the plan

```bash
uv run agent-backlog scheduler run \
  --db .agent-backlog/tasks.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 \
  --target-depth 4 \
  --max-enqueue 3
```

Expected output:

```text
created 3
```

The exact task titles include sequence numbers, for example `Refresh docs section 1`.

## 5. Inspect status and events

```bash
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 10
```

You should see `todo` tasks and recent `created` events. Generated task ids and event ids will differ between runs.

## 6. Optional: dispatch one harmless local worker

The default dispatch backend is dry-run:

```bash
uv run agent-backlog dispatch --db .agent-backlog/tasks.sqlite3 --max-workers 2
```

Dry-run dispatch records preview events and does not execute a subprocess.

To run the demo subprocess worker:

```bash
uv run agent-backlog dispatch \
  --db .agent-backlog/tasks.sqlite3 \
  --max-workers 1 \
  --backend subprocess \
  --command "bash examples/worker_echo.sh {task_id}"
```

The example worker only prints the task id. After it exits, check the queue again:

```bash
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 20
```

The subprocess backend is synchronous: the CLI waits for each selected local command to finish or time out.

## 7. Try an environment default

For a longer shell session, set the database path once:

```bash
export AGENT_BACKLOG_DB="$PWD/.agent-backlog/tasks.sqlite3"
uv run agent-backlog status
uv run agent-backlog events --limit 5
```

`--db` still wins when both are supplied.

## Start over

The demo state is disposable. If you want a fresh run without changing the existing demo store, choose a new DB path such as `.agent-backlog/tasks-2.sqlite3` and repeat the steps above.
