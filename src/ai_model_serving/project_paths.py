from __future__ import annotations

import os
from pathlib import Path


def resolve_project_root(explicit_root: Path | None = None) -> Path:
    """Return the repository/config root for source-tree and installed-package runs."""
    candidates: list[Path] = []
    if explicit_root is not None:
        candidates.append(explicit_root)
    for env_name in ("APP_CONFIG_ROOT", "PROJECT_ROOT"):
        value = os.getenv(env_name)
        if value:
            candidates.append(Path(value))
    candidates.append(Path.cwd())
    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    for candidate in candidates:
        root = candidate.resolve()
        if (root / "configs" / "model_serving.yaml").exists() and (root / "VERSION").exists():
            return root
    return (explicit_root or Path.cwd()).resolve()
