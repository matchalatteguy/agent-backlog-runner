"""Replenish a documentation audit queue and run real local workers."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from .dispatch import DispatchPolicy, dispatch_ready
from .models import BacklogPolicy, TaskStatus
from .scheduler import apply_backlog_plan, plan_backlog
from .store import init_store
from .templates import load_template_catalog

DOCUMENTS = {
    "onboarding.md": "# Onboarding\n\nInstall the package.\n\nTODO: document configuration.\n",
    "release.md": "# Release checklist\n\n## Verify\n\nRun tests and build the wheel.\n",
    "troubleshooting.md": (
        "# Troubleshooting\n\nTODO: describe failed workers.\nTODO: add an example log.\n"
    ),
}


def run_worker(db: Path, task_id: str) -> None:
    with init_store(db) as store:
        task = store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if task.body not in DOCUMENTS:
            raise ValueError("demo worker only accepts its generated document names")
        store.record_heartbeat(task_id, {"phase": "audit"})
        source = db.parent / "documents" / task.body
        lines = source.read_text(encoding="utf-8").splitlines()
        report = {
            "document": source.name,
            "headings": sum(line.startswith("#") for line in lines),
            "todo_items": sum("TODO:" in line for line in lines),
        }
        target = db.parent / "reports" / f"{source.stem}.json"
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"audited {source.name}: {report['todo_items']} follow-up items")


def run_demo(output: Path) -> dict[str, object]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "documents").mkdir()
    (output / "reports").mkdir()
    for name, content in DOCUMENTS.items():
        (output / "documents" / name).write_text(content, encoding="utf-8")
    templates = output / "templates.json"
    templates.write_text(
        json.dumps(
            {
                "templates": [
                    {
                        "slug": Path(name).stem,
                        "title": f"Audit {name}",
                        "body": name,
                        "lane": "docs",
                    }
                    for name in DOCUMENTS
                ]
            }
        ),
        encoding="utf-8",
    )
    db = output / "tasks.sqlite3"
    catalog = load_template_catalog(templates)
    with init_store(db) as store:
        policy = BacklogPolicy(min_queue_depth=1, target_queue_depth=3)
        preview = plan_backlog(store, catalog, policy)
        assert len(preview.planned) == 3 and store.list_tasks() == []
        created = apply_backlog_plan(store, plan_backlog(store, catalog, policy, dry_run=False))
        command = shlex.join(
            [
                sys.executable,
                "-m",
                "agent_backlog_runner.demo",
                "--worker-db",
                str(db),
                "--task-id",
                "{task_id}",
            ]
        )
        dispatch_ready(store, DispatchPolicy(3, "subprocess", command))
        if any(task.status != TaskStatus.DONE for task in store.list_tasks()):
            raise RuntimeError("a demo worker failed; inspect the SQLite events")
    # Reopen, as a new CLI invocation would, rather than relying on memory.
    with init_store(db) as reopened:
        completed = len(reopened.list_tasks((TaskStatus.DONE,)))
    reports = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output / "reports").glob("*.json"))
    ]
    return {
        "created_tasks": len(created),
        "completed_tasks": completed,
        "follow_up_items": sum(report["todo_items"] for report in reports),
        "reports": reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".agent-backlog/demo"))
    parser.add_argument("--worker-db", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--task-id", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker_db:
            if not args.task_id:
                raise ValueError("worker requires --task-id")
            run_worker(args.worker_db, args.task_id)
        else:
            print(json.dumps(run_demo(args.output), indent=2, sort_keys=True))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
