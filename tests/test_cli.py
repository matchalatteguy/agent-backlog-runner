from agent_backlog_runner.cli import main


def test_cli_lifecycle(tmp_path, capsys):
    db = tmp_path / "tasks.sqlite3"
    templates = tmp_path / "templates.yaml"
    templates.write_text("""
templates:
  - slug: docs-refresh
    title: "Refresh docs ${sequence}"
    body: "Review docs"
""", encoding="utf-8")
    assert main(["init", "--db", str(db)]) == 0
    assert main(["templates", "validate", "--templates", str(templates)]) == 0
    assert main(["scheduler", "plan", "--db", str(db), "--templates", str(templates), "--min-depth", "1", "--target-depth", "1", "--json"]) == 0
    assert '"planned"' in capsys.readouterr().out
    assert main(["scheduler", "run", "--db", str(db), "--templates", str(templates), "--min-depth", "1", "--target-depth", "1"]) == 0
    assert main(["status", "--db", str(db)]) == 0
    assert "todo 1" in capsys.readouterr().out


def test_cli_fails_closed_on_bad_template(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("templates: [{slug: Bad, title: A, body: B}]", encoding="utf-8")
    code = main(["templates", "validate", "--templates", str(path)])
    assert code == 1
    assert "error:" in capsys.readouterr().err
