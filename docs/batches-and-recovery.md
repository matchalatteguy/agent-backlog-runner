# Batch manifests and recovery

A batch is a finite set of independent local commands. It has no dependencies or hidden retries. Use a separate batch or a script when a later command needs another command's output.

## Manifest schema

```yaml
schema_version: 1
name: repo-checks
tasks:
  - id: unit-tests
    title: Run unit tests
    argv: ['{python}', '-m', unittest, discover, '-s', tests, '-v']
    workdir: '.'
    timeout_seconds: 300
    max_attempts: 3
    retry_backoff_seconds: 1
```

`name` and task `id` are unique-purpose lowercase slugs of 2–64 characters. Task ids must be unique within a manifest. Unknown fields and invalid types are rejected before enqueueing. Required fields are `schema_version: 1`, `name`, a non-empty `tasks` list, and each task's `id` and non-empty `argv` list.

| Task field | Default | Meaning |
|---|---|---|
| `title` | task id | Human-readable status label. |
| `argv` | required | Exact command arguments; no shell splitting or placeholder formatting. |
| `workdir` | `.` | Existing directory inside the batch root. |
| `timeout_seconds` | `300` | Positive seconds, maximum one day. |
| `max_attempts` | `3` | Total future attempts, including spawn failures; 1–100. |
| `retry_backoff_seconds` | `1` | Base delay between explicitly scheduled retries; 0–60. |

The exact argument `{python}` is replaced with the current interpreter or the `--python` override. Other braces and argument strings are literal. Workdirs resolve relative to `--root`, or to the manifest's directory if no root is supplied. Containment rejects absolute paths, `..` escapes, and escaping symlinks; it does not limit what a trusted command can do.

```bash
agent-backlog batch validate checks.yaml --root . --python "$PWD/.venv/bin/python"
agent-backlog batch load checks.yaml --root . --python "$PWD/.venv/bin/python" \
  --run-id release-check --db .agent-backlog/checks.sqlite3
```

Load validates the entire manifest and commits all new tasks atomically. IDs derive from the absolute root, manifest name, run id, and manifest task id. Loading the same run again preserves existing state; changed definitions are rejected. A new `--run-id` creates a fresh pass. Moving a checkout changes its identity; use the existing queue and recorded absolute workdirs when resuming, or create a new pass after moving it.

## Attempts and retry

Claims atomically reserve an attempt number, token, and global capacity slot. Each attempt records the interpreted argv, workdir, owner PID, worker PID/group when spawned, start/end times, outcome, exit code, log paths, observed byte counts, and truncation flags. Completion is conditional on the attempt token, so a stale completion cannot finish a later retry.

Workers receive `AGENT_BACKLOG_TASK_ID` and `AGENT_BACKLOG_ATTEMPT` in their environment. Failed commands remain failed until `retry TASK_ID` or `retry --failed` schedules another attempt. Cancelled work requires a specific task retry; `--failed` does not sweep cancellations.

The delay is `min(60, base * 2 ** (attempt_count - 1))`. Delay zero is useful for an explicitly repaired local demonstration. No retry happens automatically, and the original command definition is retained. Repair inputs/configuration/source first; choose a new run id for a changed command definition.

## Interruption and recovery

Ctrl-C tells the active coordinator to stop all of its owned workers. It records interrupted attempts as failed and preserves pending tasks. Inspect logs, repair any partial output, explicitly retry the failed work, then resume:

```bash
agent-backlog retry --db .agent-backlog/checks.sqlite3 --failed
agent-backlog dispatch --db .agent-backlog/checks.sqlite3 \
  --backend subprocess --max-workers 2 --drain
```

SIGKILL, a machine failure, or an abrupt interpreter failure can leave attempts unfinished. `stale` reports old heartbeats; it does not prove a process is dead. The coordinator updates active heartbeats about every half second.

```bash
agent-backlog stale --db .agent-backlog/checks.sqlite3 --after 30m
agent-backlog attempts --db .agent-backlog/checks.sqlite3 TASK_ID --format json
agent-backlog recover --db .agent-backlog/checks.sqlite3 TASK_ID --after 30m
```

Recovery refuses while the recorded dispatcher PID, worker PID, or process group still exists. PID reuse can cause a conservative refusal; there is no force-kill switch. Inspect and stop old work yourself before trying again. Recovery never signals saved PIDs and never queues a retry by itself. It marks the abandoned attempt and preserves its history; a later retry consumes another attempt.

A crash between claiming/spawning/binding a worker can leave its PID unknown. Untracked running tasks from the old API/schema also have no verifiable worker identity. After inspecting possible live work and partial side effects, the explicit `--acknowledge-unknown` option records your decision to abandon that uncertain work. It does not bypass a known live PID/group refusal.

Recovered work may already have changed files or external state. Retrying gives at-least-once execution; use idempotent commands or inspect and repair their effects. There are no leases spanning multiple machines and no exactly-once guarantee.

## Cancellation

```bash
agent-backlog cancel --db .agent-backlog/checks.sqlite3 TASK_ID
```

This requests cancellation in SQLite. The owning coordinator observes it and stops its own process/session; the CLI does not kill a stored PID. `attempts` confirms when it has finished. Cancelled but unfinished attempts still consume capacity. If the coordinator is gone, inspect the remaining processes and use conservative recovery. Untracked running work must be inspected/recovered before the managed cancel path can be used.

## Logs and reports

`logs TASK_ID --stream stderr --attempt 1 --tail 4096` prints a bounded tail of retained file content. The latest attempt is the default. Log files retain the first configured number of bytes, not an unlimited stream; event messages retain a separate last-500-byte snippet. Excess stream data is drained and discarded to prevent pipe deadlocks and unbounded memory/disk growth per attempt.

```bash
agent-backlog report --db .agent-backlog/checks.sqlite3 \
  --json-out .agent-backlog/report.json --md-out .agent-backlog/report.md
```

JSON includes task and attempt records. Markdown lists outcomes, exit codes, budgets, and links to local logs. These files can expose command arguments, paths, and output; they are local operational reports, not automatically sanitized publications.

## Schema migration

Opening a schema-1 database with 0.3 migrates it to schema 2. Existing tasks, events, scheduler state, and statuses are preserved. Older tasks receive a three-attempt future budget and default one-second base backoff; historical executions cannot be reconstructed. Existing running tasks remain untracked and require explicit inspection/recovery.

Stop dispatchers and keep a copy of an older database before first opening it if you need to retain a rollback point. Version 0.2 refuses the new schema; restoring the old copy discards activity recorded since that copy. See the [changelog](../CHANGELOG.md) for API and CLI compatibility changes.
