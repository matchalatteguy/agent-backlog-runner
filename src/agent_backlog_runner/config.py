from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path(".agent-backlog/tasks.sqlite3")
ENV_DB_PATH = "AGENT_BACKLOG_DB"


@dataclass(frozen=True)
class RunnerConfig:
    """Runtime defaults shared by the CLI and embedders.

    The project intentionally stays local-first, so configuration is limited to
    deterministic filesystem paths and environment variables that callers can
    override without editing code.
    """

    db_path: Path = DEFAULT_DB_PATH

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunnerConfig:
        values = os.environ if env is None else env
        raw_db_path = values.get(ENV_DB_PATH)
        return cls(db_path=Path(raw_db_path) if raw_db_path else DEFAULT_DB_PATH)


def resolve_db_path(cli_value: str | None, env: Mapping[str, str] | None = None) -> Path:
    """Return the effective SQLite path from CLI input or environment defaults."""

    if cli_value:
        return Path(cli_value)
    return RunnerConfig.from_env(env).db_path
