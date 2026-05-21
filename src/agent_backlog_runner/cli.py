from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dispatch import DispatchPolicy, dispatch_ready
from .models import BacklogPolicy
from .reports import plan_to_json, snapshot_to_json, snapshot_to_table
from .scheduler import apply_backlog_plan, plan_backlog
from .status import get_status_snapshot
from .store import init_store
from .templates import load_template_catalog


def _duration_seconds(value: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600}
    if value[-1].isdigit():
        return int(value)
    return int(value[:-1]) * units[value[-1]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-backlog")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--db", required=True)

    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--db", required=True)
    enqueue.add_argument("--title", required=True)
    enqueue.add_argument("--body", default="")
    enqueue.add_argument("--body-file")
    enqueue.add_argument("--lane", default="default")
    enqueue.add_argument("--priority", type=int, default=0)

    templates = sub.add_parser("templates")
    templates_sub = templates.add_subparsers(dest="templates_command", required=True)
    validate = templates_sub.add_parser("validate")
    validate.add_argument("--templates", required=True)

    scheduler = sub.add_parser("scheduler")
    scheduler_sub = scheduler.add_subparsers(dest="scheduler_command", required=True)
    for name in ("plan", "run"):
        cmd = scheduler_sub.add_parser(name)
        cmd.add_argument("--db", required=True)
        cmd.add_argument("--templates", required=True)
        cmd.add_argument("--min-depth", type=int, default=1)
        cmd.add_argument("--target-depth", type=int, default=3)
        cmd.add_argument("--max-enqueue", type=int, default=3)
        cmd.add_argument("--lane", action="append", default=[])
        cmd.add_argument("--tag", action="append", default=[])
        cmd.add_argument("--json", action="store_true")
        cmd.add_argument("--dry-run", action="store_true")

    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--db", required=True)
    dispatch.add_argument("--max-workers", type=int, default=1)
    dispatch.add_argument("--backend", choices=["dry-run", "subprocess"], default="dry-run")
    dispatch.add_argument("--command")
    dispatch.add_argument("--timeout", type=int, default=60)

    status = sub.add_parser("status")
    status.add_argument("--db", required=True)
    status.add_argument("--format", choices=["table", "json"], default="table")

    events = sub.add_parser("events")
    events.add_argument("--db", required=True)
    events.add_argument("--limit", type=int, default=20)

    stale = sub.add_parser("stale")
    stale.add_argument("--db", required=True)
    stale.add_argument("--after", default="30m")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            store = init_store(args.db)
            store.close()
            print(f"initialized {args.db}")
            return 0
        if args.command == "templates":
            catalog = load_template_catalog(args.templates)
            print(f"valid templates: {len(catalog.templates)}")
            return 0
        if args.command == "enqueue":
            store = init_store(args.db)
            body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
            task = store.create_task(
                title=args.title, body=body, lane=args.lane, priority=args.priority
            )
            store.close()
            print(task.id)
            return 0
        if args.command == "scheduler":
            store = init_store(args.db)
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
            store = init_store(args.db)
            result = dispatch_ready(
                store,
                DispatchPolicy(args.max_workers, args.backend, args.command, args.timeout),
            )
            store.close()
            print(
                json.dumps(
                    {"started": list(result.started), "skipped_reason": result.skipped_reason}
                )
            )
            return 0
        if args.command == "status":
            store = init_store(args.db)
            snapshot = get_status_snapshot(store)
            store.close()
            print(
                snapshot_to_json(snapshot) if args.format == "json" else snapshot_to_table(snapshot)
            )
            return 0
        if args.command == "events":
            store = init_store(args.db)
            events = store.events(limit=args.limit)
            store.close()
            for event in events:
                print(f"{event.id} {event.task_id} {event.event_type} {event.message}")
            return 0
        if args.command == "stale":
            store = init_store(args.db)
            snapshot = get_status_snapshot(store, stale_after=_duration_seconds(args.after))
            store.close()
            for item in snapshot.stale_tasks:
                print(f"{item.task.id} age={item.age_seconds}s")
            return 0
        return 2
    except Exception as exc:  # fail closed for CLI users
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
