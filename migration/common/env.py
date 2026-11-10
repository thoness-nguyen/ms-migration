"""Minimal .env loader for the operator tools (discovery, provisioning,
membership sync). The migration engine itself (tenant-migrator / panel) reads
credentials only from the process environment - see CLAUDE.md."""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overwriting variables that
    are already set (so an exported value always wins over the file)."""
    if not path.exists():
        raise FileNotFoundError(f"Environment file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
