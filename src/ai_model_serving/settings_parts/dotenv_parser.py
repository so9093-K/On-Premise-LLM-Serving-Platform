from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class EnvParseResult:
    values: dict[str, str]
    errors: list[str]


def parse_env_file(path: Path) -> EnvParseResult:
    """Parse the project's strict dotenv subset.

    This is intentionally narrower than Docker Compose's .env grammar. Auth and
    exposure settings are safety gates, so ambiguous duplicate/quoted/commented
    control-plane values are configuration errors instead of precedence puzzles.
    """

    values: dict[str, str] = {}
    errors: list[str] = []
    if not path.exists():
        return EnvParseResult(values, errors)
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            errors.append(f"{path}:{line_no}: export syntax is not supported; use KEY=VALUE.")
            continue
        if "=" not in line:
            errors.append(f"{path}:{line_no}: expected KEY=VALUE.")
            continue
        key_part, value_part = line.split("=", 1)
        key = key_part.strip()
        value = value_part.strip()
        if key != key_part or value != value_part:
            errors.append(f"{path}:{line_no}: spaces around KEY=VALUE are not supported.")
            continue
        if not ENV_KEY_RE.fullmatch(key):
            errors.append(f"{path}:{line_no}: invalid env key {key!r}.")
            continue
        if key in values:
            errors.append(f"{path}:{line_no}: duplicate env key {key!r}.")
            continue
        if value.startswith(("'", '"')) or value.endswith(("'", '"')):
            errors.append(f"{path}:{line_no}: quoted values are not supported for {key}.")
            continue
        if "#" in value:
            errors.append(f"{path}:{line_no}: inline comments are not supported for {key}.")
            continue
        values[key] = value
    return EnvParseResult(values, errors)


def load_strict_env_file(path: Path) -> dict[str, str]:
    result = parse_env_file(path)
    if result.errors:
        raise RuntimeError("\n".join(result.errors))
    return result.values
