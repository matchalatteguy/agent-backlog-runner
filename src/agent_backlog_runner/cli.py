from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from .batches import enqueue_batch, load_batch_manifest
from .config import ENV_DB_PATH, resolve_db_path
from .dispatch import DispatchPolicy, dispatch_ready
from .models import BacklogPolicy, TaskStatus
from .recovery import recover_task
from .reports import plan_to_json, snapshot_to_json, snapshot_to_table, write_queue_report
from .scheduler import apply_backlog_plan, plan_backlog
from .status import get_status_snapshot
from .store import init_store
from .templates import load_template_catalog


def _task_to_dict(task) -> dict[str, object]:
    return {
        "id": task.id,
        "title": task.title,
        "body": task.body,
        "status": task.status.value,
        "priority": task.priority,
        "lane": task.lane,
        "tags": list(task.tags),
        "role": task.role,
        "workdir": task.workdir,
        "command": task.command,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "heartbeat_at": task.heartbeat_at,
        "argv": list(task.command_argv) if task.command_argv else None,
        "attempt_count": task.attempt_count,
        "max_attempts": task.max_attempts,
        "not_before": task.not_before,
        "timeout_seconds": task.timeout_seconds,
        "retry_backoff_seconds": task.retry_backoff_seconds,
    }


def _event_to_dict(event) -> dict[str, object]:
    return {
        "id": event.id,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "message": event.message,
        "payload": event.payload,
        "created_at": event.created_at,
    }


def _print_task_table(tasks) -> None:
    print("id              status     lane       priority  title")
    print("--------------  ---------  ---------  --------  -----")
    for task in tasks:
        print(
            f"{task.id:14}  {task.status.value:9}  {task.lane:9}  {task.priority:8}  {task.title}"
        )


def _duration_seconds(value: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600}
    if value[-1].isdigit():
        return int(value)
    return int(value[:-1]) * units[value[-1]]


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        help=(
            "SQLite database path. Defaults to "
            f"${ENV_DB_PATH} when set, otherwise .agent-backlog/tasks.sqlite3."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-backlog")
    parser.add_argument("--version", action="version", version=version("agent-backlog-runner"))
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    _add_db_argument(init)

    demo = sub.add_parser("demo", help="run the repository-check repair and resume walkthrough")
    demo.add_argument("--output", type=Path, default=Path(".agent-backlog/maintenance-demo"))

    enqueue = sub.add_parser("enqueue")
    _add_db_argument(enqueue)
    enqueue.add_argument("--title", required=True)
    enqueue.add_argument("--body", default="")
    enqueue.add_argument("--body-file")
    enqueue.add_argument("--lane", default="default")
    enqueue.add_argument("--role", default="agent")
    enqueue.add_argument("--tag", action="append", default=[])
    enqueue.add_argument("--priority", type=int, default=0)
    enqueue.add_argument("--command", dest="task_command")
    enqueue.add_argument("--workdir")
    enqueue.add_argument("--max-attempts", type=int, default=3)
    enqueue.add_argument("--retry-backoff", type=float, default=1)
    enqueue.add_argument("--timeout", type=float)

    batch = sub.add_parser("batch", help="load a finite, repeatable command manifest")
    batch_sub = batch.add_subparsers(dest="batch_command", required=True)
    for name in ("validate", "load"):
        cmd = batch_sub.add_parser(name)
        _add_db_argument(cmd)
        cmd.add_argument("manifest")
        cmd.add_argument("--root")
        cmd.add_argument(
            "--python", help="interpreter substituted for the exact argv token {python}"
        )
        cmd.add_argument("--run-id", default="default")

    list_cmd = sub.add_parser("list")
    _add_db_argument(list_cmd)
    list_cmd.add_argument("--status", action="append", default=[])
    list_cmd.add_argument("--format", choices=["table", "json"], default="table")

    show = sub.add_parser("show")
    _add_db_argument(show)
    show.add_argument("task_id")
    show.add_argument("--format", choices=["table", "json"], default="table")

    mark = sub.add_parser("mark")
    _add_db_argument(mark)
    mark.add_argument("task_id")
    mark.add_argument("--status", required=True, choices=[status.value for status in TaskStatus])
    mark.add_argument("--message", default="")

    heartbeat = sub.add_parser("heartbeat")
    _add_db_argument(heartbeat)
    heartbeat.add_argument("task_id")
    heartbeat.add_argument(
        "--payload",
        default="{}",
        help="JSON object recorded with the heartbeat event.",
    )

    templates = sub.add_parser("templates")
    templates_sub = templates.add_subparsers(dest="templates_command", required=True)
    validate = templates_sub.add_parser("validate")
    validate.add_argument("--templates", required=True)

    scheduler = sub.add_parser("scheduler")
    scheduler_sub = scheduler.add_subparsers(dest="scheduler_command", required=True)
    for name in ("plan", "run"):
        cmd = scheduler_sub.add_parser(name)
        _add_db_argument(cmd)
        cmd.add_argument("--templates", required=True)
        cmd.add_argument("--min-depth", type=int, default=1)
        cmd.add_argument("--target-depth", type=int, default=3)
        cmd.add_argument("--max-enqueue", type=int, default=3)
        cmd.add_argument("--lane", action="append", default=[])
        cmd.add_argument("--tag", action="append", default=[])
        cmd.add_argument("--json", action="store_true")
        cmd.add_argument("--dry-run", action="store_true")

    dispatch = sub.add_parser("dispatch")
    _add_db_argument(dispatch)
    dispatch.add_argument("--max-workers", type=int, default=1)
    dispatch.add_argument("--backend", choices=["dry-run", "subprocess"], default="dry-run")
    dispatch.add_argument("--command", dest="worker_command")
    dispatch.add_argument("--timeout", type=float, default=60)
    dispatch.add_argument(
        "--drain",
        action="store_true",
        help="finish the queued snapshot, filling slots as workers exit",
    )
    dispatch.add_argument("--log-root", type=Path)
    dispatch.add_argument("--max-log-bytes", type=int, default=1048576)

    retry = sub.add_parser("retry", help="explicitly schedule a failed or cancelled task")
    _add_db_argument(retry)
    retry.add_argument("task_id", nargs="?")
    retry.add_argument(
        "--failed", action="store_true", help="schedule all failed tasks with remaining attempts"
    )

    recover = sub.add_parser(
        "recover", help="abandon stale work only after its recorded processes are gone"
    )
    _add_db_argument(recover)
    recover.add_argument("task_id")
    recover.add_argument("--after", default="30m")
    recover.add_argument("--acknowledge-unknown", action="store_true")

    cancel = sub.add_parser("cancel", help="ask the owning dispatcher to stop a task")
    _add_db_argument(cancel)
    cancel.add_argument("task_id")

    attempts = sub.add_parser("attempts", help="inspect durable attempt outcomes and log locations")
    _add_db_argument(attempts)
    attempts.add_argument("task_id", nargs="?")
    attempts.add_argument("--format", choices=["table", "json"], default="table")

    logs = sub.add_parser("logs", help="read a bounded tail of a worker's retained output")
    _add_db_argument(logs)
    logs.add_argument("task_id")
    logs.add_argument("--attempt", type=int)
    logs.add_argument("--stream", choices=["stdout", "stderr"], default="stdout")
    logs.add_argument("--tail", type=int, default=4096)

    report = sub.add_parser("report", help="write an inspectable JSON or Markdown queue report")
    _add_db_argument(report)
    report.add_argument("--json-out", type=Path)
    report.add_argument("--md-out", type=Path)

    status = sub.add_parser("status")
    _add_db_argument(status)
    status.add_argument("--format", choices=["table", "json"], default="table")

    events = sub.add_parser("events")
    _add_db_argument(events)
    events.add_argument("--limit", type=int, default=20)
    events.add_argument("--task-id")
    events.add_argument("--format", choices=["table", "json"], default="table")

    stale = sub.add_parser("stale")
    _add_db_argument(stale)
    stale.add_argument("--after", default="30m")
    stale.add_argument("--format", choices=["table", "json"], default="table")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = resolve_db_path(args.db) if hasattr(args, "db") else None
    try:
        if args.command == "demo":
            from .maintenance import run_demo

            print(json.dumps(run_demo(args.output), indent=2))
            return 0
        if args.command == "report":
            with init_store(db_path) as store:
                write_queue_report(store, json_out=args.json_out, md_out=args.md_out)
            return 0
        if args.command == "batch":
            manifest = load_batch_manifest(args.manifest, root=args.root, python=args.python)
            if args.batch_command == "validate":
                print(json.dumps(asdict(manifest), indent=2))
            else:
                with init_store(db_path) as store:
                    records = enqueue_batch(store, manifest, run_id=args.run_id)
                    print(json.dumps([_task_to_dict(task) for task in records], indent=2))
            return 0
        if args.command == "retry":
            if bool(args.task_id) == args.failed:
                raise ValueError("choose one task_id or --failed")
            with init_store(db_path) as store:
                tasks = (
                    store.list_tasks((TaskStatus.FAILED,))
                    if args.failed
                    else [store.get_task(args.task_id)]
                )
                scheduled, skipped = [], []
                for task in tasks:
                    if task is None:
                        raise KeyError(args.task_id)
                    try:
                        scheduled.append(_task_to_dict(store.retry_task(task.id)))
                    except ValueError as exc:
                        if not args.failed:
                            raise
                        skipped.append({"id": task.id, "reason": str(exc)})
                print(json.dumps({"scheduled": scheduled, "skipped": skipped}, indent=2))
            return 0
        if args.command == "recover":
            with init_store(db_path) as store:
                recover_task(
                    store,
                    args.task_id,
                    stale_after=_duration_seconds(args.after),
                    acknowledge_unknown=args.acknowledge_unknown,
                )
            print(f"recovered {args.task_id}; inspect outputs, then retry explicitly")
            return 0
        if args.command == "cancel":
            with init_store(db_path) as store:
                store.request_cancel(args.task_id)
            print(
                f"cancellation requested for {args.task_id}; attempts shows when the worker stops"
            )
            return 0
        if args.command == "attempts":
            with init_store(db_path) as store:
                attempts = store.attempts(args.task_id)
            if args.format == "json":
                print(json.dumps(attempts, indent=2))
            else:
                print("task             attempt  outcome       exit  stdout / stderr")
                for attempt in attempts:
                    print(
                        f"{attempt['task_id']} {attempt['attempt_no']:7}  {attempt['outcome']:12}  "
                        f"{str(attempt['exit_code']):4}  {attempt['stdout_path']} / "
                        f"{attempt['stderr_path']}"
                    )
            return 0
        if args.command == "logs":
            if not 1 <= args.tail <= 67108864:
                raise ValueError("--tail must be from 1 to 67108864 bytes")
            with init_store(db_path) as store:
                attempts = store.attempts(args.task_id)
            matches = [
                item
                for item in attempts
                if args.attempt is None or item["attempt_no"] == args.attempt
            ]
            if not matches or not matches[0][args.stream + "_path"]:
                raise ValueError("no retained log for the selected attempt")
            with Path(matches[0][args.stream + "_path"]).open("rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - args.tail))
                print(handle.read(args.tail).decode("utf-8", "replace"), end="")
            return 0
        if args.command == "init":
            store = init_store(db_path)
            store.close()
            print(f"initialized {db_path}")
            return 0
        if args.command == "templates":
            catalog = load_template_catalog(args.templates)
            print(f"valid templates: {len(catalog.templates)}")
            return 0
        if args.command == "enqueue":
            store = init_store(db_path)
            body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
            task = store.create_task(
                title=args.title,
                body=body,
                lane=args.lane,
                tags=tuple(args.tag),
                role=args.role,
                priority=args.priority,
                command=args.task_command,
                workdir=args.workdir,
                max_attempts=args.max_attempts,
                retry_backoff_seconds=args.retry_backoff,
                timeout_seconds=args.timeout,
            )
            store.close()
            print(task.id)
            return 0
        if args.command == "list":
            store = init_store(db_path)
            statuses = tuple(TaskStatus(value) for value in args.status)
            tasks = store.list_tasks(statuses)
            store.close()
            if args.format == "json":
                print(json.dumps([_task_to_dict(task) for task in tasks], indent=2, sort_keys=True))
            else:
                _print_task_table(tasks)
            return 0
        if args.command == "show":
            store = init_store(db_path)
            task = store.get_task(args.task_id)
            events = store.events(args.task_id, limit=20) if task is not None else []
            attempts = store.attempts(args.task_id) if task is not None else []
            store.close()
            if task is None:
                raise KeyError(args.task_id)
            if args.format == "json":
                print(
                    json.dumps(
                        {
                            "task": _task_to_dict(task),
                            "events": [_event_to_dict(event) for event in events],
                            "attempts": attempts,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                _print_task_table([task])
                for event in events:
                    print(f"{event.id} {event.event_type} {event.message}")
            return 0
        if args.command == "mark":
            store = init_store(db_path)
            task = store.get_task(args.task_id)
            if task is None:
                raise KeyError(args.task_id)
            if any(attempt["finished_at"] is None for attempt in store.attempts(args.task_id)):
                raise ValueError("managed attempt is active; use cancel to request a stop")
            if args.status == "todo" and task.attempt_count:
                raise ValueError("use retry to respect attempt budgets and backoff")
            store.mark_task(args.task_id, TaskStatus(args.status), args.message)
            store.close()
            print(f"marked {args.task_id} {args.status}")
            return 0
        if args.command == "heartbeat":
            payload = json.loads(args.payload)
            if not isinstance(payload, dict):
                raise ValueError("--payload must decode to a JSON object")
            store = init_store(db_path)
            store.record_heartbeat(args.task_id, payload)
            store.close()
            print(f"heartbeat {args.task_id}")
            return 0
        if args.command == "scheduler":
            store = init_store(db_path)
            catalog = load_template_catalog(args.templates)
            policy = BacklogPolicy(
                min_queue_depth=args.min_depth,
                target_queue_depth=args.target_depth,
                max_enqueue_per_cycle=args.max_enqueue,
                lanes=tuple(args.lane),
                tags=tuple(args.tag),
            )
            plan = plan_backlog(
                store, catalog, policy, dry_run=args.scheduler_command == "plan" or args.dry_run
            )
            if args.scheduler_command == "run" and not args.dry_run:
                created = apply_backlog_plan(store, plan)
                print("created " + str(len(created)))
            else:
                print(plan_to_json(plan) if args.json else f"planned {len(plan.planned)} tasks")
            store.close()
            return 0
        if args.command == "dispatch":
            store = init_store(db_path)
            result = dispatch_ready(
                store,
                DispatchPolicy(
                    args.max_workers,
                    args.backend,
                    args.worker_command,
                    args.timeout,
                    args.drain,
                    args.log_root,
                    args.max_log_bytes,
                ),
            )
            store.close()
            print(json.dumps(asdict(result)))
            return 1 if result.failed or result.cancelled else (2 if result.skipped_reason else 0)
        if args.command == "status":
            store = init_store(db_path)
            snapshot = get_status_snapshot(store)
            store.close()
            print(
                snapshot_to_json(snapshot) if args.format == "json" else snapshot_to_table(snapshot)
            )
            return 0
        if args.command == "events":
            store = init_store(db_path)
            events = store.events(args.task_id, limit=args.limit)
            store.close()
            if args.format == "json":
                print(
                    json.dumps(
                        [_event_to_dict(event) for event in events],
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                for event in events:
                    print(f"{event.id} {event.task_id} {event.event_type} {event.message}")
            return 0
        if args.command == "stale":
            store = init_store(db_path)
            snapshot = get_status_snapshot(store, stale_after=_duration_seconds(args.after))
            store.close()
            if args.format == "json":
                print(
                    json.dumps(
                        [
                            {"task": _task_to_dict(item.task), "age_seconds": item.age_seconds}
                            for item in snapshot.stale_tasks
                        ],
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                for item in snapshot.stale_tasks:
                    print(f"{item.task.id} age={item.age_seconds}s")
            return 0
        return 2
    except KeyboardInterrupt:
        print(
            "dispatcher interrupted; inspect attempts and retry unfinished tasks explicitly",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:  # fail closed for CLI users
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
