from __future__ import annotations

import re
from pathlib import Path

SAFE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
SAFE_TASK_ID_RE = re.compile(r"^task_[a-z0-9]{12}$")


def require_safe_slug(value: str, *, field: str = "slug") -> str:
    if not SAFE_SLUG_RE.fullmatch(value):
        raise ValueError(f"unsafe {field}: use 2-64 lowercase letters, numbers, '-' or '_'")
    return value


def require_safe_task_id(value: str) -> str:
    if not SAFE_TASK_ID_RE.fullmatch(value):
        raise ValueError("unsafe task id")
    return value


def safe_child_path(root: Path, child: str | Path) -> Path:
    root = root.expanduser().resolve()
    candidate = Path(child)
    if candidate.is_absolute():
        raise ValueError("absolute child paths are not allowed")
    resolved = (root / candidate).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("path escapes configured root")
    return resolved
