from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import PurePosixPath

from .common import ROOT


_DOCKERFILE = ROOT / "ops/images/vllm-unified/Dockerfile"
_REQUIRED_MANIFEST_ENTRIES = {
    ".dockerignore",
    "ops/images/vllm-unified/Dockerfile",
}


def _logical_dockerfile_instructions() -> list[tuple[int, str]]:
    instructions: list[tuple[int, str]] = []
    pending = ""
    start_line = 0
    for line_number, raw_line in enumerate(
        _DOCKERFILE.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if not pending:
            start_line = line_number
        continued = stripped.endswith("\\")
        piece = stripped[:-1].rstrip() if continued else stripped
        pending = f"{pending} {piece}".strip()
        if not continued:
            instructions.append((start_line, pending))
            pending = ""
    if pending:
        raise SystemExit(
            f"unterminated Dockerfile continuation at {_DOCKERFILE}:{start_line}"
        )
    return instructions


def _strip_copy_flags(raw: str, *, line_number: int) -> tuple[bool, str]:
    text = raw.lstrip()
    from_stage = False
    while text.startswith("--"):
        flag, separator, remainder = text.partition(" ")
        if not separator:
            raise SystemExit(
                f"invalid COPY flags at {_DOCKERFILE}:{line_number}: {raw!r}"
            )
        if flag == "--from":
            stage, stage_separator, after_stage = remainder.lstrip().partition(" ")
            if not stage or not stage_separator:
                raise SystemExit(
                    f"invalid COPY --from at {_DOCKERFILE}:{line_number}: {raw!r}"
                )
            from_stage = True
            text = after_stage.lstrip()
            continue
        if flag.startswith("--from="):
            from_stage = True
        elif "=" not in flag:
            raise SystemExit(
                "COPY flags with separate values are not supported by this validator; "
                f"use --flag=value or extend validation: {_DOCKERFILE}:{line_number}: {flag}"
            )
        text = remainder.lstrip()
    return from_stage, text


def _normalize_local_source(raw: str, *, line_number: int) -> str:
    if any(token in raw for token in ("*", "?", "[", "]")):
        raise SystemExit(
            "vLLM Dockerfile COPY source must be an exact repository file so deploy "
            f"drift can compare it deterministically: {_DOCKERFILE}:{line_number}: {raw!r}"
        )
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SystemExit(
            f"unsafe local COPY source at {_DOCKERFILE}:{line_number}: {raw!r}"
        )
    return path.as_posix()


def _dockerfile_local_copy_sources() -> set[str]:
    sources: set[str] = set()
    for line_number, instruction in _logical_dockerfile_instructions():
        keyword, separator, remainder = instruction.partition(" ")
        if keyword.upper() != "COPY":
            continue
        if not separator:
            raise SystemExit(f"invalid COPY at {_DOCKERFILE}:{line_number}")
        from_stage, arguments = _strip_copy_flags(remainder, line_number=line_number)
        if from_stage:
            continue
        try:
            if arguments.startswith("["):
                parsed = json.loads(arguments)
                if not isinstance(parsed, list) or not all(
                    isinstance(value, str) for value in parsed
                ):
                    raise ValueError("JSON COPY form must be a string list")
                parts = parsed
            else:
                parts = shlex.split(arguments, posix=True)
        except ValueError as exc:
            raise SystemExit(
                f"cannot parse COPY at {_DOCKERFILE}:{line_number}: {exc}"
            ) from exc
        if len(parts) < 2:
            raise SystemExit(
                f"COPY must contain source and destination at {_DOCKERFILE}:{line_number}"
            )
        for source in parts[:-1]:
            sources.add(_normalize_local_source(source, line_number=line_number))
    return sources


def _declared_source_manifest() -> list[str]:
    result = subprocess.run(
        [
            "bash",
            "-c",
            "source scripts/lib/deploy_recreate_policy.sh; vllm_unified_image_source_paths",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise SystemExit(f"cannot read unified vLLM source manifest: {detail}")
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not paths:
        raise SystemExit("unified vLLM source manifest must not be empty")
    if len(paths) != len(set(paths)):
        raise SystemExit("unified vLLM source manifest contains duplicate paths")
    for raw in paths:
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise SystemExit(f"unsafe unified vLLM source manifest path: {raw!r}")
        if not (ROOT / path).is_file():
            raise SystemExit(f"unified vLLM source manifest path is not a file: {raw}")
    return paths


def validate_vllm_unified_build_inputs() -> None:
    """Keep deploy source-drift authority aligned with the Docker build context."""
    declared = set(_declared_source_manifest())
    missing_controls = _REQUIRED_MANIFEST_ENTRIES - declared
    if missing_controls:
        raise SystemExit(
            "unified vLLM source manifest is missing build control files: "
            + ", ".join(sorted(missing_controls))
        )

    missing_copy_sources = _dockerfile_local_copy_sources() - declared
    if missing_copy_sources:
        raise SystemExit(
            "unified vLLM source manifest must include every local Dockerfile COPY source; "
            "missing: " + ", ".join(sorted(missing_copy_sources))
        )
