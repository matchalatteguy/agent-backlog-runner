import json
import subprocess
import sys


def test_real_workers_write_reports_and_persist_completion(tmp_path):
    output = tmp_path / "demo"
    completed = subprocess.run(
        [sys.executable, "-m", "agent_backlog_runner.demo", "--output", str(output)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert report == {
        "completed_tasks": 3,
        "created_tasks": 3,
        "follow_up_items": 3,
        "reports": [
            {"document": "onboarding.md", "headings": 1, "todo_items": 1},
            {"document": "release.md", "headings": 2, "todo_items": 0},
            {"document": "troubleshooting.md", "headings": 1, "todo_items": 2},
        ],
    }
    assert len(list((output / "reports").glob("*.json"))) == 3
    repeated = subprocess.run(
        [sys.executable, "-m", "agent_backlog_runner.demo", "--output", str(output)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert repeated.returncode == 1
