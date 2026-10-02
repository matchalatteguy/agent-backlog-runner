# Agent Backlog Runner

[![CI](https://github.com/matchalatteguy/agent-backlog-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/matchalatteguy/agent-backlog-runner/actions/workflows/ci.yml)

A local command queue for repository checks and repeatable maintenance. Load a finite batch, run independent commands in parallel, inspect each attempt's exit code and logs, repair failures, and resume without repeating completed work. SQLite stores the queue and attempt history across CLI invocations.

Use it when a shell loop is becoming hard to resume or debug. Templates can also replenish a recurring backlog below a chosen depth. Commands run while the dispatcher is open; detached sessions, workspace provisioning, dependency graphs, and distributed workers are outside this tool's scope.

## Install and try the repair/resume walkthrough

Python 3.11+ on Linux or macOS. Version 0.3 is an alpha release; unfinished attempts require operator inspection and explicit recovery.

```bash
uv tool install 'git+https://github.com/matchalatteguy/agent-backlog-runner.git@v0.3.0'
agent-backlog --help
```

From a checkout, use `uv sync --locked --extra dev` and prefix commands with `uv run`.

The packaged demonstration requires no test framework or external service:

```bash
agent-backlog demo --output .agent-backlog/maintenance-demo
```

From a checkout, run `uv run agent-backlog demo --output .agent-backlog/maintenance-demo`.

The walkthrough creates a tiny Python project, runs its unit tests, reopens the queue, then runs syntax and README-link checks. The missing usage document fails its check. It writes the document, explicitly schedules one retry, and resumes through another SQLite connection:

```text
checks: 3
initial_completed: 1
resume_failed: 1
repaired_checks: 1
attempts: 4
final_done: 3
```

[Implementation](src/agent_backlog_runner/maintenance.py). These are real commands on illustrative files, not a claim of production use. Inspect `before-repair.md`, `after-repair.md`, and `logs/` under the output directory. The demo refuses to overwrite an existing output directory.

## Run checks on your own repository

Save a trusted manifest as `checks.yaml`:

```yaml
schema_version: 1
name: repo-checks
tasks:
  - id: unit-tests
    argv: ['{python}', '-m', pytest, '-q']
    timeout_seconds: 600
    max_attempts: 3
    retry_backoff_seconds: 2
  - id: lint-check
    argv: ['{python}', '-m', ruff, check, '.']
    timeout_seconds: 120
    max_attempts: 3
```

Use your project's interpreter, with its test dependencies installed:

```bash
agent-backlog batch load checks.yaml --root . \
  --python "$PWD/.venv/bin/python" --run-id first-pass \
  --db .agent-backlog/checks.sqlite3

# Preview; commands do not run until --backend subprocess is selected.
agent-backlog dispatch --db .agent-backlog/checks.sqlite3 --max-workers 2 --drain
agent-backlog dispatch --db .agent-backlog/checks.sqlite3 \
  --backend subprocess --max-workers 2 --drain

agent-backlog status --db .agent-backlog/checks.sqlite3
agent-backlog attempts --db .agent-backlog/checks.sqlite3
agent-backlog report --db .agent-backlog/checks.sqlite3 \
  --json-out .agent-backlog/checks.json --md-out .agent-backlog/checks.md
```

`--max-workers 2` really runs two commands concurrently. Claims and the shared capacity are reserved atomically; cancelled attempts retain their slots until stopped. Concurrent coordinators must use the same cap. Avoid putting commands that write the same files into one parallel batch.

Reloading the same manifest/root/run id is idempotent. It preserves task statuses and rejects changed command definitions. Choose a new `--run-id` for a new full check pass. The [checked-in manifest](examples/repo-checks.yaml) runs this repository's tests and lint after installing development dependencies.

## Repair, retry, and resume

A failed command stays failed. Inspect its task id and retained stderr:

```bash
agent-backlog attempts --db .agent-backlog/checks.sqlite3 --format json
agent-backlog logs --db .agent-backlog/checks.sqlite3 TASK_ID --stream stderr
# Repair the source or configuration, then schedule failed tasks with budget remaining.
agent-backlog retry --db .agent-backlog/checks.sqlite3 --failed
agent-backlog dispatch --db .agent-backlog/checks.sqlite3 \
  --backend subprocess --max-workers 2 --drain
```

Retry is explicit, bounded by `max_attempts`, and delayed by exponential backoff capped at 60 seconds. Spawn failures also consume attempts. `--drain` waits for scheduled retries in its initial queued snapshot, fills slots as workers finish, and leaves completed tasks untouched. Without `--drain`, one ready batch is selected; delayed work does not block later ready tasks.

Ctrl-C stops owned workers and records interrupted attempts as failed. Queued tasks remain ready. An abrupt dispatcher crash leaves unfinished work requiring [explicit recovery](docs/batches-and-recovery.md#interruption-and-recovery), because its side effects may already have happened. This is at-least-once execution after an operator chooses recovery and retry.

| Dispatch exit | Meaning |
|---|---|
| `0` | Selected work passed, preview completed, or no eligible work was selected. |
| `1` | A selected worker failed/cancelled, or a CLI input/operation failed. |
| `2` | Capacity or exhausted attempts prevented dispatch. |
| `130` | Ctrl-C interrupted the dispatcher. |

Use `status` to distinguish an empty queue from future scheduled work. JSON dispatch output lists started, completed, failed, and cancelled task ids.

## Recurring template work

The original queue-replenishment workflow remains available:

```bash
agent-backlog scheduler plan --db .agent-backlog/recurring.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 --target-depth 4 --max-enqueue 3 --json
agent-backlog scheduler run --db .agent-backlog/recurring.sqlite3 \
  --templates examples/templates/basic-backlog.yaml \
  --min-depth 2 --target-depth 4 --max-enqueue 3
```

Templates create work descriptions. Supply per-task commands or a dispatch command when executing them. Plans check queue depth and duplicate titles; applying a stale plan is rejected. The scheduler cursor is shared across catalogs and filters. [Template schema](docs/template-schema.md) · [Scheduler policy](docs/scheduler-policy.md).

## Logs, processes, and trust

Each attempt retains stdout and stderr in separate files, up to 1 MiB per stream by default. Excess bytes are drained and discarded; attempts record observed byte counts and truncation flags. `--max-log-bytes` changes the per-stream cap up to 64 MiB. Full output is available only below the cap. Historical attempt files are retained until you remove or archive them yourself.

Timeout and cancellation stop the owning dispatcher's process group on POSIX. Workers that escape their group, change privileges, or leave uninterruptible OS operations are outside that guarantee. On interpreters with `waitid(WNOWAIT)`, normal exit also cleans up remaining group members before the leader is reaped. Older macOS interpreters lack that operation: normal-exit background children cannot be safely adopted; commands must wait for their children. Windows supports queue inspection/planning but not subprocess dispatch in this release.

Subprocess dispatch requires the default `SIGCHLD` handler. Embedded callers must not reap its children independently; lost ownership prevents group signals and leaves unfinished work for inspection.

Commands and manifests are trusted local code, with normal access to your files, environment, and network. Workdir containment prevents accidental manifest path escapes; it is not a process sandbox. No shell is inserted automatically. Legacy command placeholders are substituted after argument splitting. SQLite, logs, and exported reports can contain command arguments, local paths, and sensitive output; keep them in a protected ignored directory.

## Development and documentation

```bash
uv sync --locked --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build --no-sources
```

CI tests Python 3.11–3.14 on Linux, plus process ownership and the repair/resume walkthrough on macOS 3.14. It covers overlapping workers, competing capacity claims, interruption, conservative recovery, bounded logs, and retries; installed-wheel examples run outside the checkout. [0.3 migration](CHANGELOG.md) · [Batch manifests and recovery](docs/batches-and-recovery.md) · [Dispatch](docs/dispatch-backends.md) · [Python API](docs/python-api.md) · [Troubleshooting](docs/troubleshooting.md) · [MIT license](LICENSE).
