from pathlib import Path


def test_public_files_avoid_private_or_sensitive_terms():
    root = Path(__file__).resolve().parents[1]
    blocked = [
        "all" + "things" + "tra" + "ding",
        "/" + "home" + "/",
        "/" + "mnt" + "/",
        "personal" + "-account" + "-name",
        "wall" + "et",
        "sign" + "ing",
        "can" + "ary",
        "order" + "-capable",
        "private" + " repo",
    ]
    checked = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix not in {".pyc"} and ".git" not in path.parts:
            if any(
                part
                in {
                    ".venv",
                    ".pytest_cache",
                    ".ruff_cache",
                    ".agent-backlog",
                    ".wheel-smoke",
                    "dist",
                    "build",
                }
                for part in path.parts
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            checked.append(path)
            lowered = text.lower()
            assert not any(term.lower() in lowered for term in blocked), path
    assert checked
