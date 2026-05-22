import time

from agent_backlog_runner.cli import main


def test_cli_uses_environment_default_db(tmp_path, monkeypatch, capsys):
    db = tmp_path / "env-default.sqlite3"
    monkeypatch.setenv("AGENT_BACKLOG_DB", str(db))

    assert main(["init"]) == 0

    assert db.exists()
    assert f"initialized {db}" in capsys.readouterr().out


def test_cli_lifecycle(tmp_path, capsys):
    db = tmp_path / "tasks.sqlite3"
    templates = tmp_path / "templates.yaml"
    templates.write_text(
        """
templates:
  - slug: docs-refresh
    title: "Refresh docs ${sequence}"
    body: "Review docs"
""",
        encoding="utf-8",
    )
    assert main(["init", "--db", str(db)]) == 0
    assert main(["templates", "validate", "--templates", str(templates)]) == 0
    assert (
        main(
            [
                "scheduler",
                "plan",
                "--db",
                str(db),
                "--templates",
                str(templates),
                "--min-depth",
                "1",
                "--target-depth",
                "1",
                "--json",
            ]
        )
        == 0
    )
    assert '"planned"' in capsys.readouterr().out
    assert (
        main(
            [
                "scheduler",
                "run",
                "--db",
                str(db),
                "--templates",
                str(templates),
                "--min-depth",
                "1",
                "--target-depth",
                "1",
            ]
        )
        == 0
    )
    assert main(["status", "--db", str(db)]) == 0
    assert "todo 1" in capsys.readouterr().out


def test_cli_dispatch_subprocess_command(tmp_path, capsys):
    db = tmp_path / "tasks.sqlite3"
    body = tmp_path / "body.txt"
    body.write_text("Review one safe local note.", encoding="utf-8")

    assert main(["init", "--db", str(db)]) == 0
    assert (
        main(
            [
                "enqueue",
                "--db",
                str(db),
                "--title",
                "Review local note",
                "--body-file",
                str(body),
            ]
        )
        == 0
    )
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
                "python -c 'import sys; print(sys.argv[1])' {task_id}",
            ]
        )
        == 0
    )
    assert main(["status", "--db", str(db)]) == 0
    assert main(["dispatch", "--db", str(db), "--max-workers", "1", "--backend", "dry-run"]) == 0
    assert '"started"' in capsys.readouterr().out


def test_cli_enqueue_can_store_task_specific_worker_command(tmp_path, capsys):
    db = tmp_path / "tasks.sqlite3"
    marker = tmp_path / "marker.txt"

    assert main(["init", "--db", str(db)]) == 0
    assert (
        main(
            [
                "enqueue",
                "--db",
                str(db),
                "--title",
                "Write marker",
                "--body",
                "Write one local marker file.",
                "--lane",
                "ops",
                "--role",
                "worker",
                "--tag",
                "demo",
                "--workdir",
                str(tmp_path),
                "--command",
                (
                    'python -c "from pathlib import Path; '
                    "Path('marker.txt').write_text("
                    "'{task_id}|{lane}|{role}|{workdir}', encoding='utf-8')\""
                ),
            ]
        )
        == 0
    )
    assert main(["dispatch", "--db", str(db), "--max-workers", "1", "--backend", "subprocess"]) == 0

    marker_text = marker.read_text(encoding="utf-8")
    assert "task_" in marker_text
    assert f"|ops|worker|{tmp_path}" in marker_text
    assert '"started"' in capsys.readouterr().out


def test_cli_fails_closed_on_bad_template(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("templates: [{slug: Bad, title: A, body: B}]", encoding="utf-8")
    code = main(["templates", "validate", "--templates", str(path)])
    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_cli_list_show_mark_and_heartbeat_json(tmp_path, capsys):
    db = tmp_path / "tasks.sqlite3"
    assert main(["init", "--db", str(db)]) == 0
    assert main(["enqueue", "--db", str(db), "--title", "Inspect task", "--body", "Body"]) == 0
    task_id = capsys.readouterr().out.strip().splitlines()[-1]

    assert main(["list", "--db", str(db), "--format", "json"]) == 0
    assert '"title": "Inspect task"' in capsys.readouterr().out

    assert main(["show", "--db", str(db), task_id, "--format", "json"]) == 0
    shown = capsys.readouterr().out
    assert f'"id": "{task_id}"' in shown
    assert '"events"' in shown

    assert (
        main(
            [
                "mark",
                "--db",
                str(db),
                task_id,
                "--status",
                "running",
                "--message",
                "claimed",
            ]
        )
        == 0
    )
    assert main(["heartbeat", "--db", str(db), task_id, "--payload", '{"step": 2}']) == 0
    assert main(["events", "--db", str(db), "--format", "json"]) == 0
    events = capsys.readouterr().out
    assert '"event_type": "heartbeat"' in events
    assert '"step": 2' in events


def test_cli_stale_json_reports_running_task(tmp_path, capsys):
    db = tmp_path / "tasks.sqlite3"
    assert main(["init", "--db", str(db)]) == 0
    assert main(["enqueue", "--db", str(db), "--title", "Stale task", "--body", "Body"]) == 0
    task_id = capsys.readouterr().out.strip().splitlines()[-1]
    assert main(["mark", "--db", str(db), task_id, "--status", "running"]) == 0
    time.sleep(1.1)
    assert main(["stale", "--db", str(db), "--after", "0", "--format", "json"]) == 0
    assert f'"id": "{task_id}"' in capsys.readouterr().out
