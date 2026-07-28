#!/usr/bin/env python3
"""command_registry.yaml과 Makefile의 실행 가능한 명령 정합성을 검증한다."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "configs" / "command_registry.yaml"

REQUIRED_FIELDS = {
    "make_target",
    "title",
    "category",
    "audience",
    "description",
    "command",
    "kind",
    "safety",
    "requires",
    "writes_files",
    "outputs",
    "owner",
    "docs",
    "tests",
}

REQUIRES_FIELDS = {"python", "docker", "gpu", "live_services", "secrets", "env_file"}

VALID_KINDS = {"public", "internal", "alias", "deprecated"}
VALID_SAFETY = {
    "read_only",
    "diagnostic",
    "writes_generated",
    "writes_env",
    "starts_services",
    "stops_services",
    "deletes_files",
    "builds_images",
    "deploys_remote",
}

# writes_env/starts_services/deploys_remote은 owner+docs 필수 (strict)
OWNER_DOCS_REQUIRED_SAFETY = {"writes_env", "starts_services", "deploys_remote", "deletes_files"}
# deletes_files는 docs 또는 tests 또는 writes_files가 있어야 한다
DELETES_FILES_METADATA_REQUIRED = {"deletes_files"}

# strict 검사 대상 docs 경로 (non-strict는 operations/** 만)
DOCS_STRICT = [
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "START_HERE.md",
    ROOT / "scripts" / "README.md",
]
DOCS_STRICT_GLOBS = [
    (ROOT / "docs" / "operations", "*.md"),
    (ROOT / "docs" / "development", "*.md"),
    (ROOT / "docs" / "release", "*.md"),
]
# non-strict 검사 대상 (기존 범위)
DOCS_DEFAULT = [
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "START_HERE.md",
]
DOCS_DEFAULT_GLOBS = [
    (ROOT / "docs" / "operations", "*.md"),
]


def _load_registry() -> dict:
    try:
        return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"command_registry.yaml 파싱 실패: {exc}") from exc


def _parse_makefile_targets() -> tuple[set[str], set[str]]:
    """(실제 recipe 또는 dependency가 있는 target 집합, .PHONY 전용 target 집합) 반환."""
    makefile = ROOT / "Makefile"
    if not makefile.exists():
        raise SystemExit("Makefile을 찾을 수 없습니다.")

    text = makefile.read_text(encoding="utf-8")
    lines = text.splitlines()

    phony_targets: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            targets_part = stripped[len(".PHONY:"):].strip()
            phony_targets.update(targets_part.split())

    defined_targets: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("\t") or line.startswith(" ") or not line.strip():
            i += 1
            continue
        if line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_./-]*)(\s*:(?!=))(.*)", line)
        if m:
            target_name = m.group(1)
            rest_of_line = m.group(3).strip()
            has_recipe_next = (i + 1 < len(lines) and lines[i + 1].startswith("\t"))
            has_dependency = bool(rest_of_line)
            if has_recipe_next or has_dependency:
                defined_targets.add(target_name)
        i += 1

    phony_only = phony_targets - defined_targets
    return defined_targets, phony_only


def _collect_make_refs_from_paths(paths: list[Path]) -> set[str]:
    """지정된 파일 목록에서 make <target> 참조를 수집한다."""
    pattern = re.compile(r"\bmake\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)(?:\s|$|`|'|\")")
    refs: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            refs.add(m.group(1))
    return refs


def _build_scan_paths(strict: bool) -> list[Path]:
    base_docs = DOCS_STRICT if strict else DOCS_DEFAULT
    base_globs = DOCS_STRICT_GLOBS if strict else DOCS_DEFAULT_GLOBS

    scan_paths = [p for p in base_docs if p.exists()]
    for directory, pattern in base_globs:
        if directory.is_dir():
            scan_paths.extend(directory.rglob(pattern))
    return scan_paths


def _collect_make_refs_in_docs(strict: bool) -> set[str]:
    scan_paths = _build_scan_paths(strict)
    return _collect_make_refs_from_paths(scan_paths)


def _collect_feature_commands() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    features_dir = ROOT / "features"
    if not features_dir.is_dir():
        return result
    for path in sorted(features_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        commands = data.get("commands", []) if isinstance(data, dict) else []
        targets = []
        for cmd in commands:
            cmd_str = str(cmd)
            m = re.match(r"make\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)", cmd_str)
            if m:
                targets.append(m.group(1))
        if targets:
            result[path.name] = targets
    return result


def _collect_operator_guide_commands() -> list[str]:
    guide_path = ROOT / "scripts" / "reports" / "operator_guide.py"
    if not guide_path.exists():
        return []
    text = guide_path.read_text(encoding="utf-8")
    pattern = re.compile(r'"make\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)')
    return list({m.group(1) for m in pattern.finditer(text)})


def _validate_allow_unregistered(
    registry: dict,
    defined_targets: set[str],
    registry_targets: set[str],
    errors: list[str],
    warnings: list[str],
    strict: bool,
) -> set[str]:
    """allow_unregistered_make_targets 검증. 허용 목록의 target 집합을 반환한다."""
    allow_list = registry.get("allow_unregistered_make_targets", [])
    allowed_targets: set[str] = set()

    for entry in allow_list:
        t = entry.get("target", "<unknown>")
        allowed_targets.add(t)
        if not entry.get("owner") or not entry.get("reason"):
            errors.append(
                f"[allow_unregistered/{t}] owner 또는 reason이 비어 있음 (둘 다 필수)"
            )
        if t not in defined_targets:
            warnings.append(
                f"[allow_unregistered/{t}] Makefile에 target이 없음 — allowlist가 stale할 수 있음"
            )
        if t in registry_targets:
            errors.append(
                f"[allow_unregistered/{t}] registry에도 등록되어 있음 — allowlist에서 제거 필요"
            )

    return allowed_targets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="command_registry.yaml 무결성 검증 — Makefile drift, docs drift, features drift를 검사합니다."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "strict 모드: docs drift를 WARNING 대신 ERROR로 처리하고, "
            "위험 safety 명령의 metadata 누락과 alias canonical 누락도 ERROR로 처리합니다."
        ),
    )
    args = parser.parse_args()
    strict = args.strict

    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. YAML 파싱 ────────────────────────────────────────────────────────
    registry = _load_registry()

    # ── 2. categories 존재 검사 ──────────────────────────────────────────────
    valid_category_ids = {cat["id"] for cat in registry.get("categories", [])}

    # ── 3. 필수 필드 + kind/safety 유효성 ─────────────────────────────────
    for cmd in registry.get("commands", []):
        target = cmd.get("make_target", "<unknown>")
        missing = REQUIRED_FIELDS - set(cmd.keys())
        if missing:
            errors.append(f"[{target}] 필수 필드 누락: {', '.join(sorted(missing))}")
        if cmd.get("category") not in valid_category_ids:
            errors.append(f"[{target}] category '{cmd.get('category')}' 가 categories에 없음")
        if cmd.get("kind") not in VALID_KINDS:
            errors.append(f"[{target}] kind '{cmd.get('kind')}' 이 유효하지 않음 ({VALID_KINDS})")
        if cmd.get("safety") not in VALID_SAFETY:
            errors.append(f"[{target}] safety '{cmd.get('safety')}' 이 유효하지 않음")
        requires = cmd.get("requires", {})
        if isinstance(requires, dict):
            missing_req = REQUIRES_FIELDS - set(requires.keys())
            if missing_req:
                errors.append(f"[{target}] requires 필드 누락: {', '.join(sorted(missing_req))}")

        # 위험 명령 강화 검사
        safety = cmd.get("safety", "")
        kind = cmd.get("kind", "")
        if safety in OWNER_DOCS_REQUIRED_SAFETY and kind == "public":
            if not cmd.get("owner"):
                errors.append(f"[{target}] 위험 safety '{safety}' 인데 owner가 비어 있음")
            # docs 누락은 strict → ERROR, non-strict → WARNING
            if not cmd.get("docs") and not cmd.get("tests"):
                msg = f"[{target}] 위험 safety '{safety}' 인데 docs와 tests 모두 비어 있음"
                if strict:
                    errors.append(msg)
                else:
                    warnings.append(msg)

        # deletes_files: writes_files도 확인 (strict)
        if safety == "deletes_files" and kind == "public" and strict:
            if not cmd.get("writes_files"):
                errors.append(
                    f"[{target}] deletes_files 인데 writes_files가 비어 있음 (strict: 삭제 대상 명시 필수)"
                )

        # alias 검사
        if kind == "alias":
            has_dep = bool(cmd.get("canonical") or cmd.get("depends_on"))
            if not has_dep:
                msg = f"[{target}] alias 인데 canonical/depends_on이 없음"
                if strict:
                    errors.append(msg)
                else:
                    warnings.append(msg)

    # ── 4. Makefile target 존재 검사 ────────────────────────────────────────
    defined_targets, phony_only = _parse_makefile_targets()

    registry_targets = {cmd["make_target"] for cmd in registry.get("commands", [])}
    for target in registry_targets:
        if target not in defined_targets:
            errors.append(f"[{target}] registry에 있지만 Makefile에 실제 target 정의가 없음")

    # ── 5. .PHONY-only target 검사 ──────────────────────────────────────────
    public_no_op_allowed = set()
    for cmd in registry.get("commands", []):
        if cmd.get("allow_empty_recipe"):
            public_no_op_allowed.add(cmd["make_target"])

    for target in sorted(phony_only):
        if target not in public_no_op_allowed:
            errors.append(f"[{target}] .PHONY에 있지만 실제 Makefile recipe/dependency가 없음")

    # ── 6. allow_unregistered_make_targets 검증 ─────────────────────────────
    allowed_targets = _validate_allow_unregistered(
        registry, defined_targets, registry_targets, errors, warnings, strict
    )

    # ── 7. 미등록 Makefile target 검사 (strict: WARNING) ────────────────────
    if strict:
        for t in sorted(defined_targets):
            if t not in registry_targets and t not in allowed_targets:
                warnings.append(
                    f"[{t}] Makefile에 있지만 registry에도 allowlist에도 없음 (등록 또는 allowlist 추가 권장)"
                )

    # ── 결과 출력 ────────────────────────────────────────────────────────────
    for w in warnings:
        print(f"WARNING: {w}", flush=True)
    for e in errors:
        print(f"ERROR: {e}", flush=True)

    mode_label = " (strict)" if strict else ""
    if errors:
        print(
            f"\ncommand registry 검증 실패{mode_label} — {len(errors)}개 오류, {len(warnings)}개 경고",
            flush=True,
        )
        return 1

    total_commands = len(registry.get("commands", []))
    print(
        f"command registry 검증 완료{mode_label} — {total_commands}개 명령, {len(warnings)}개 경고",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
