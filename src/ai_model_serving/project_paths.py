from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path


_DEFAULT_REQUIRED_PATHS = ("VERSION", "configs/model_serving.yaml")


def resolve_project_root(
    explicit_root: Path | None = None,
    *,
    required_paths: Iterable[str | Path] = _DEFAULT_REQUIRED_PATHS,
    strict: bool = False,
) -> Path:
    """Return the repository/config root for source-tree and installed-package runs.

    Callers may declare the markers they actually require instead of reimplementing
    parent-directory discovery. Runtime code keeps the historical non-strict fallback;
    validation/generation entrypoints can opt into ``strict=True`` so a missing root
    fails loudly instead of silently selecting the current directory.
    """

    required = tuple(Path(path) for path in required_paths)
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

    seen: set[Path] = set()
    for candidate in candidates:
        root = candidate.resolve()
        if root in seen:
            continue
        seen.add(root)
        if all((root / marker).exists() for marker in required):
            return root

    fallback = (explicit_root or Path.cwd()).resolve()
    if strict:
        markers = ", ".join(str(path) for path in required)
        raise RuntimeError(f"could not locate project root containing: {markers}")
    return fallback
