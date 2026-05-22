import json
from pathlib import Path

from agent_backlog_runner.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_TEMPLATES = REPO_ROOT / "examples" / "templates" / "basic-backlog.yaml"
EXAMPLE_WORKER = REPO_ROOT / "examples" / "worker_echo.sh"


def test_first_five_minutes_example_flow_creates_and_dispatches_tasks(tmp_path, capsys):
    db = tmp_path / "tasks.sqlite3"

    assert main(["init", "--db", str(db)]) == 0
    assert main(["templates", "validate", "--templates", str(EXAMPLE_TEMPLATES)]) == 0
    assert (
        main(
            [
                "scheduler",
                "plan",
                "--db",
                str(db),
                "--templates",
                str(EXAMPLE_TEMPLATES),
                "--min-depth",
                "2",
                "--target-depth",
                "4",
                "--max-enqueue",
                "3",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    plan_output = capsys.readouterr().out
    assert '"dry_run": true' in plan_output
    assert len(json.loads(plan_output[plan_output.index("{") :])["planned"]) == 3

    assert (
        main(
            [
                "scheduler",
                "run",
                "--db",
                str(db),
                "--templates",
                str(EXAMPLE_TEMPLATES),
                "--min-depth",
                "2",
                "--target-depth",
                "4",
                "--max-enqueue",
                "3",
            ]
        )
        == 0
    )
    assert "created 3" in capsys.readouterr().out

    assert main(["status", "--db", str(db)]) == 0
    assert "todo 3" in capsys.readouterr().out

    assert (
        main(
            [
                "dispatch",
                "--db",
                str(db),
                "--max-workers",
                "1",
                "--backend",
                "subprocess",
                "--command",
                f"bash {EXAMPLE_WORKER} {{task_id}}",
            ]
        )
        == 0
    )
    dispatch_output = capsys.readouterr().out
    dispatch_result = json.loads(dispatch_output[dispatch_output.index("{") :])
    assert dispatch_result["started"][0].startswith("task_")

    assert main(["status", "--db", str(db)]) == 0
    final_status = capsys.readouterr().out
    assert "done 1" in final_status
    assert "todo 2" in final_status
