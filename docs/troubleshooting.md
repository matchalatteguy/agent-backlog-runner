# Troubleshooting

This page lists common local setup and demo failures. Agent Backlog Runner is intentionally local-first, so most problems are path, template, database, or subprocess issues.

## `uv` is not installed

Symptoms:

```text
uv: command not found
```

Install `uv` using the official installer for your platform, then rerun:

```bash
uv sync --extra dev
uv run agent-backlog --help
```

If you prefer another environment manager, install the package in an isolated Python 3.11+ environment and run the `agent-backlog` console script from there.

## The CLI cannot find the database

Most commands create the SQLite file when needed, but parent directory permissions and path typos can still fail.

Use an explicit path first:

```bash
uv run agent-backlog init --db .agent-backlog/tasks.sqlite3
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
```

Then, if you want a shared shell default:

```bash
export AGENT_BACKLOG_DB="$PWD/.agent-backlog/tasks.sqlite3"
uv run agent-backlog status
```

Resolution order is `--db`, then `AGENT_BACKLOG_DB`, then `.agent-backlog/tasks.sqlite3`.

## Template validation fails

Run validation before scheduler commands:

```bash
uv run agent-backlog templates validate --templates examples/templates/basic-backlog.yaml
```

Common causes:

- YAML indentation errors.
- Missing top-level `templates:` list.
- Unsafe `slug`, `lane`, `role`, or tag values. Use lowercase letters, digits, hyphens, and underscores.
- Missing required task fields such as `slug`, `title`, or `body`.

The loader fails closed: it rejects malformed catalogs instead of partially enqueueing unclear work.

## `scheduler run` creates fewer tasks than expected

This is usually expected. The planner considers active depth (`todo + running`) and duplicate titles.

Check status:

```bash
uv run agent-backlog status --db .agent-backlog/tasks.sqlite3
```

Then preview with JSON:

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

If the active depth is already at or above the minimum, the plan may intentionally create nothing. If a generated title already exists, it is skipped to avoid simple duplicates.

## Dispatch does not start a subprocess

Start with dry-run mode:

```bash
uv run agent-backlog dispatch --db .agent-backlog/tasks.sqlite3 --max-workers 1
```

If `skipped_reason` says the concurrency cap was reached, there are already enough `running` tasks for the configured `--max-workers`.

For subprocess mode, provide either a global command or per-task command:

```bash
uv run agent-backlog dispatch \
  --db .agent-backlog/tasks.sqlite3 \
  --backend subprocess \
  --max-workers 1 \
  --command "bash examples/worker_echo.sh {task_id}"
```

Without a command, subprocess dispatch fails closed because it does not know what worker to run.

## Command placeholder errors

Supported placeholders are:

```text
{task_id} {title} {lane} {role} {priority} {workdir}
```

Unknown placeholders fail before a subprocess starts. This is intentional, so typos do not silently become strange shell arguments.

Prefer fixed commands that receive `{task_id}`:

```bash
--command "bash examples/worker_echo.sh {task_id}"
```

Avoid command templates that treat task title/body content as executable input. Task metadata should be data, not code.

## A subprocess times out or fails

Set a realistic timeout:

```bash
uv run agent-backlog dispatch \
  --db .agent-backlog/tasks.sqlite3 \
  --backend subprocess \
  --command "bash examples/worker_echo.sh {task_id}" \
  --timeout 120
```

Failed subprocesses mark the task as `failed` and store the tail of stderr/stdout in events. Inspect recent events:

```bash
uv run agent-backlog events --db .agent-backlog/tasks.sqlite3 --limit 20
```

The built-in dispatcher is synchronous. A long command blocks the current CLI process until it exits or times out.

## Stale tasks appear

Stale tasks are `running` tasks whose latest update/heartbeat is older than the threshold.

```bash
uv run agent-backlog stale --db .agent-backlog/tasks.sqlite3 --after 30m
```

This command reports stale tasks only. It does not kill workers or requeue tasks automatically. Use `agent-backlog mark TASK_ID --status todo|failed|blocked` after you decide whether to retry, fail, or investigate the task.

## Start the demo over

The demo stores runtime state under `.agent-backlog/`, which is ignored by version control. To start fresh without deleting anything, choose a new DB path:

```bash
uv run agent-backlog init --db .agent-backlog/tasks-2.sqlite3
```

If you do delete a local demo DB, make sure you are deleting only disposable runtime state, not a repository or source directory.
