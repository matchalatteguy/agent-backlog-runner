"""Small checks and an executable failure/retry/resume walkthrough."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

from .batches import enqueue_batch, load_batch_manifest
from .dispatch import DispatchPolicy, dispatch_ready
from .models import TaskStatus
from .reports import write_queue_report
from .store import init_store


def check_syntax(directory: Path) -> dict[str, object]:
    files = sorted(directory.rglob("*.py"))
    if not files:
        raise ValueError("no Python source files found")
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {"check": "python-syntax", "files": len(files), "passed": True}


def check_links(document: Path) -> dict[str, object]:
    """Check local inline Markdown file links; web URLs and heading fragments are skipped."""
    missing, checked = [], 0
    for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
        target = target.strip().strip("<>")
        if not target or re.match(r"[a-zA-Z]+:", target) or target.startswith("#"):
            continue
        target = target.split("#", 1)[0]
        checked += 1
        if not (document.parent / target).exists():
            missing.append(target)
    if missing:
        raise ValueError("missing linked files: " + ", ".join(missing))
    return {"check": "local-document-links", "links": checked, "passed": True}


def run_demo(output: Path) -> dict[str, object]:
    """Perform real syntax, unittest, and local-link checks on a tiny example project."""
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "docs").mkdir()
    (project / "src" / "totals.py").write_text(
        "def total(values):\n    return sum(values)\n", encoding="utf-8"
    )
    (project / "tests" / "test_totals.py").write_text(
        "import sys, unittest\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        "from totals import total\n\nclass TotalsTests(unittest.TestCase):\n"
        "    def test_empty(self):\n        self.assertEqual(total([]), 0)\n"
        "    def test_values(self):\n        self.assertEqual(total([3, -1, 4]), 6)\n",
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        "# Totals example\n\n[Usage](docs/usage.md)\n", encoding="utf-8"
    )
    manifest_path = output / "checks.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "example-checks",
                "tasks": [
                    {
                        "id": "unit-tests",
                        "title": "Run project unit tests",
                        "argv": ["{python}", "-m", "unittest", "discover", "-s", "tests", "-v"],
                        "retry_backoff_seconds": 0,
                    },
                    {
                        "id": "python-syntax",
                        "title": "Parse source files",
                        "argv": [
                            "{python}",
                            "-m",
                            "agent_backlog_runner.maintenance",
                            "syntax",
                            "src",
                        ],
                        "retry_backoff_seconds": 0,
                    },
                    {
                        "id": "document-links",
                        "title": "Check README file links",
                        "argv": [
                            "{python}",
                            "-m",
                            "agent_backlog_runner.maintenance",
                            "links",
                            "README.md",
                        ],
                        "retry_backoff_seconds": 0,
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    db = output / "checks.sqlite3"
    with init_store(db) as store:
        tasks = enqueue_batch(store, load_batch_manifest(manifest_path, root=project))
        first = dispatch_ready(store, DispatchPolicy(1, "subprocess"))
        assert len(first.completed) == 1
    # Resume through a fresh connection: the completed check does not execute again.
    with init_store(db) as store:
        resumed = dispatch_ready(store, DispatchPolicy(2, "subprocess", drain=True))
        if len(resumed.failed) != 1:
            raise RuntimeError("expected the missing usage document to fail its local-link check")
        failure = store.get_task(resumed.failed[0])
        assert failure is not None
        write_queue_report(
            store, json_out=output / "before-repair.json", md_out=output / "before-repair.md"
        )
    (project / "docs" / "usage.md").write_text(
        "# Usage\n\nImport `total` and pass a sequence of numbers.\n", encoding="utf-8"
    )
    with init_store(db) as store:
        store.retry_task(failure.id)
    with init_store(db) as store:
        repaired = dispatch_ready(store, DispatchPolicy(2, "subprocess", drain=True))
        if repaired.failed or any(task.status != TaskStatus.DONE for task in store.list_tasks()):
            raise RuntimeError("repair failed; inspect attempts and log files")
        write_queue_report(
            store, json_out=output / "after-repair.json", md_out=output / "after-repair.md"
        )
        attempts = store.attempts()
        return {
            "checks": len(tasks),
            "initial_completed": len(first.completed),
            "resume_failed": len(resumed.failed),
            "repaired_checks": len(repaired.completed),
            "attempts": len(attempts),
            "final_done": len(store.list_tasks((TaskStatus.DONE,))),
            "database": str(db),
            "report": str(output / "after-repair.md"),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("syntax", "links"):
        cmd = sub.add_parser(name)
        cmd.add_argument("path", type=Path)
    demo = sub.add_parser("demo")
    demo.add_argument("--output", type=Path, default=Path(".agent-backlog/maintenance-demo"))
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            result = run_demo(args.output)
        elif args.command == "syntax":
            result = check_syntax(args.path)
        else:
            result = check_links(args.path)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, SyntaxError, RuntimeError) as exc:
        print(f"check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
