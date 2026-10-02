# Changelog

## 0.3.0

- Run local commands concurrently with a real shared SQLite capacity cap. `--drain`
  processes a finite queued snapshot, filling slots as workers exit.
- Add idempotent finite command manifests, explicit bounded retries with capped
  exponential backoff, per-task timeouts, and durable token-owned attempt history.
- Store separate capped stdout/stderr files, drain both streams without unbounded
  capture, and record observed bytes/truncation plus short event tails.
- Add `cancel`, conservative `recover`, `attempts`, `logs`, `report`, `demo`, and
  `--version` CLI paths. Cancellation is handled by the owning coordinator;
  recovery never signals stored PIDs and never silently retries uncertain work.
- Stop owned POSIX process groups on timeout/cancel/interruption. Keep leaders
  unreaped during group signalling; document the older-macOS normal-exit fallback.
- Add an executable repository-maintenance repair/resume walkthrough and a
  reusable pytest/Ruff manifest, plus targeted concurrency/crash/log/retry tests.

### Compatibility and migration

Schema 1 migrates automatically to schema 2, preserving task/event/scheduler data.
Old tasks receive three future attempts and a one-second base backoff; past
execution history cannot be reconstructed. Old running tasks retain their state
and require explicit inspection and unknown-identity recovery. Version 0.2
refuses schema 2; retain an earlier copy before upgrading if rollback is needed.

Default dispatch still selects a bounded batch and defaults to preview, but its
workers now execute concurrently. Only `--drain` waits for future retry due times.
Worker failure/cancellation now returns CLI exit `1`, blocked capacity/budget `2`,
and Ctrl-C `130`. `DispatchResult` adds completed/failed/cancelled ids; existing
`started` and `skipped_reason` fields remain. Existing string commands/placeholders
and scheduler APIs remain available. Duplicate YAML/JSON fields are now rejected.

For managed attempts, use `cancel`, `retry`, and `recover`; the CLI rejects raw
status edits that would bypass live-attempt ownership or retry budgets. Low-level
manual `mark_task`/`claim_task` bookkeeping APIs remain for compatibility.

## 0.2.0

- Claim a task and a capacity slot atomically before a subprocess starts.
- Record worker timeouts and spawn errors as `failed` instead of leaving tasks
  permanently `running`; continue the rest of the selected batch.
- Decode worker output as UTF-8 with replacement for malformed bytes, preserving
  successful completion and later tasks in the batch.
- Preserve operator status changes made while a worker is running.
- Deduplicate titles within a plan, reject stale plans, and apply tasks, events,
  and the scheduler cursor in one transaction.
- Advance the cursor when a cycle finds only previously used titles, allowing
  later cycles to move past them and replenish the queue.
- Refuse to overwrite a database's unsupported future schema version.
- Add a documentation audit demo that executes real workers, wheel smoke checks,
  and CI.

### API change

`apply_backlog_plan()` now leaves a plan marked `dry_run=True` unchanged. To
create tasks, explicitly call `plan_backlog(..., dry_run=False)` before applying
the plan. Applying an already applied plan, or one based on a changed queue,
raises `ValueError`; produce a fresh plan and try again.

The SQLite table layout and schema version remain unchanged at version 1.
`mark_task()` now returns a boolean; callers that previously ignored its return
value continue to work. The optional `expected_status` argument supports guarded
updates.
