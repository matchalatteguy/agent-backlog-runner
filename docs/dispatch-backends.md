# Dispatch backends

The default `dry-run` backend records preview events and leaves tasks `todo`. Commands run only with `--backend subprocess`.

```bash
agent-backlog dispatch --db .agent-backlog/checks.sqlite3 --max-workers 2 --drain
agent-backlog dispatch --db .agent-backlog/checks.sqlite3 \
  --backend subprocess --max-workers 2 --drain
```

## Concurrent bounded execution

The CLI stays open while selected commands run concurrently. A shared SQLite transaction reserves each task, attempt token, and capacity slot. Competing coordinators cannot execute the same queued task, and active coordinators must agree on `--max-workers`.

Without `--drain`, dispatch selects at most the available number of ready tasks. Future scheduled retries do not block eligible work. With `--drain`, it takes a finite snapshot of queued tasks and refills its slots until that snapshot is consumed, waiting for its explicit retry due times. Newly enqueued work is left to a later invocation. Other coordinators or unrecovered attempts can prevent dispatch; the CLI reports the capacity reason rather than adopting their processes.

Tasks are selected by descending priority, then creation time and insertion order. Independent commands must not concurrently overwrite the same files. There is no DAG or lock manager for command side effects.

## Command forms

A batch's `argv` list is passed directly to `Popen`, with no shell. The exact manifest token `{python}` is resolved when loading the manifest. Legacy per-task `command` strings remain supported, with placeholders `{task_id}`, `{title}`, `{lane}`, `{role}`, `{priority}`, and `{workdir}`. Quoting is parsed before placeholder substitution, preserving each value as one argument. A dispatch `--command` overrides stored commands/argv.

These commands are trusted local code. Do not interpolate untrusted text into shell or interpreter program arguments. Workers inherit the coordinator's environment and access, receive a closed stdin, and run in the recorded workdir. Commands requiring interactive input are unsuitable.

## Timeouts, output, and cancellation

Each task can override the dispatch timeout. Nonzero exits, timeouts, missing executables, and invalid workdirs become failed attempts. The remaining selected tasks continue. The coordinator reads both output streams concurrently into separate capped files; excess data is drained/discarded, and truncation is recorded.

Cancel through `agent-backlog cancel TASK_ID --db ...`. The request is observed by the owning coordinator, which stops its own child process group. It does not signal a saved PID. Cancelled work retains its capacity slot until the attempt finishes.

POSIX workers start in a new session. Cleanup sends TERM, allows a brief grace period, then KILL while the leader remains unreaped. This retains PID ownership during group signalling. On Darwin, a zombie-only group can return EPERM; the coordinator checks only that owned group's process states before treating that condition as already stopped. An actual permission denial with live members remains a cleanup error, and the attempt is left unfinished for inspection.

Subprocess dispatch requires the default `SIGCHLD` handler and rejects ignored or custom handlers before claiming work. Embedding callers must not reap the dispatcher's children from another thread. Where `waitid(WNOWAIT)` is available, cleanup confirms child ownership before signalling; an already-reaped child leaves its attempt unfinished for inspection. The dispatcher never replaces a caller's signal handler.

Where `waitid(WNOWAIT)` is available, normal completion is observed before reaping and remaining group members are cleaned up. Older macOS interpreters without it reap through `Popen.poll`; after that, the old group is never signalled because its PID could be reused. On those interpreters, commands must wait for their children before exiting. Escaped sessions, changed privileges, and uninterruptible OS operations are outside cleanup guarantees. Windows subprocess dispatch is unsupported; queue/planning commands remain available.

Ctrl-C attempts cleanup of every owned worker, even if one cleanup fails. Cleanup failures are reported and remain inspectable; a still-live worker is not marked stopped. Abrupt failure can leave unfinished attempts requiring [explicit recovery](batches-and-recovery.md#interruption-and-recovery).

## Results

Dispatch prints JSON with `started`, `completed`, `failed`, `cancelled`, and `skipped_reason`. Exit `0` means the selected work/preview passed or no eligible tasks were selected; `1` reports selected failure/cancellation or a CLI operation error, `2` blocked capacity/budget, and `130` Ctrl-C. Inspect `status` for remaining delayed tasks; success of one batch is not proof the whole queue is empty.

See [batch manifests, retry, logs, and recovery](batches-and-recovery.md). Templates still support [recurring depth-based replenishment](scheduler-policy.md), independently of finite command batches.
