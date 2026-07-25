"""Command Registry 계약 테스트 — registry 무결성, Makefile 정합성, docs drift를 검사합니다."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "configs" / "command_registry.yaml"
PYTHON = sys.executable

DOCS_STRICT_BASES = [
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
DOCS_ARCHIVE_DIRS = {ROOT / "docs" / "archive", ROOT / "reports" / "archive"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def _parse_makefile_targets() -> tuple[set[str], set[str]]:
    """(recipe 또는 dependency가 있는 실제 target, .PHONY-only target) 반환."""
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    lines = text.splitlines()

    phony_targets: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            phony_targets.update(stripped[len(".PHONY:"):].strip().split())

    defined_targets: set[str] = set()
    for i, line in enumerate(lines):
        if line.startswith(("\t", " ")) or not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_./-]*)(\s*:(?!=))(.*)", line)
        if m:
            target_name = m.group(1)
            rest = m.group(3).strip()
            has_recipe = i + 1 < len(lines) and lines[i + 1].startswith("\t")
            if has_recipe or rest:
                defined_targets.add(target_name)

    return defined_targets, phony_targets - defined_targets


def _collect_make_refs_from_paths(paths: list[Path]) -> set[str]:
    pattern = re.compile(r"\bmake\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)(?:\s|$|`|'|\")")
    refs: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            refs.add(m.group(1))
    return refs


def _is_archive(path: Path) -> bool:
    for d in DOCS_ARCHIVE_DIRS:
        try:
            path.relative_to(d)
            return True
        except ValueError:
            pass
    return False


def _build_strict_scan_paths() -> list[Path]:
    paths = [p for p in DOCS_STRICT_BASES if p.exists()]
    for directory, pattern in DOCS_STRICT_GLOBS:
        if directory.is_dir():
            paths.extend(p for p in directory.rglob(pattern) if not _is_archive(p))
    return paths


# ── 기본 구조 ──────────────────────────────────────────────────────────────

def test_registry_yaml_parseable():
    assert REGISTRY_PATH.exists(), "configs/command_registry.yaml 가 없습니다"
    registry = _load_registry()
    assert "schema_version" in registry
    assert "categories" in registry
    assert "commands" in registry
    assert len(registry["commands"]) > 0


def test_registry_has_required_categories():
    registry = _load_registry()
    ids = {cat["id"] for cat in registry["categories"]}
    expected = {"init", "validate_test_build", "start_stop", "readiness", "maintenance"}
    assert expected.issubset(ids), f"필수 category 누락: {expected - ids}"


# ── make help / help-full / help-json ──────────────────────────────────────

def test_make_help_succeeds():
    result = subprocess.run(
        ["make", "help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHON": PYTHON},
    )
    assert result.returncode == 0, f"make help 실패:\n{result.stderr}"
    output = result.stdout
    assert len(output) > 100, "make help 출력이 너무 짧습니다"


def test_make_help_full_succeeds():
    result = subprocess.run(
        ["make", "help-full"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHON": PYTHON},
    )
    assert result.returncode == 0, f"make help-full 실패:\n{result.stderr}"
    output = result.stdout
    assert "## " in output, "help-full 출력에 category 헤딩이 없습니다"


def test_make_help_json_is_parseable():
    result = subprocess.run(
        ["make", "--no-print-directory", "help-json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHON": PYTHON},
    )
    assert result.returncode == 0, f"make help-json 실패:\n{result.stderr}"
    data = json.loads(result.stdout)
    assert "commands" in data
    assert "categories" in data
    assert len(data["commands"]) > 0


def test_make_help_full_contains_public_commands():
    registry = _load_registry()
    public_targets = {
        cmd["make_target"] for cmd in registry["commands"] if cmd.get("kind") == "public"
    }
    result = subprocess.run(
        ["make", "help-full"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHON": PYTHON},
    )
    assert result.returncode == 0
    output = result.stdout
    missing_from_help = [t for t in public_targets if t not in output]
    assert not missing_from_help, f"help-full 출력에 없는 public target: {missing_from_help}"


# ── validate_command_registry.py ─────────────────────────────────────────

def test_validate_command_registry_succeeds():
    result = subprocess.run(
        [PYTHON, "scripts/commands/validate_command_registry.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"validate_command_registry.py 실패:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_command_check_strict_no_warnings():
    """make command-check (--strict) 가 WARNING 없이 성공해야 한다."""
    result = subprocess.run(
        [PYTHON, "scripts/commands/validate_command_registry.py", "--strict"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"validate_command_registry.py --strict 실패:\n{result.stdout}\n{result.stderr}"
    )
    warning_lines = [l for l in result.stdout.splitlines() if l.startswith("WARNING:")]
    assert not warning_lines, (
        f"strict 모드에서 WARNING이 남아 있습니다:\n" + "\n".join(warning_lines)
    )


# ── compose-diagnostics recipe 검사 ────────────────────────────────────────

def test_compose_diagnostics_has_recipe():
    defined_targets, phony_only = _parse_makefile_targets()
    assert "compose-diagnostics" in defined_targets, (
        "compose-diagnostics 가 Makefile에 실제 recipe/dependency 없이 .PHONY에만 있습니다"
    )
    assert "compose-diagnostics" not in phony_only


# ── .PHONY-only target 없음 ──────────────────────────────────────────────

def test_no_phony_only_targets():
    _, phony_only = _parse_makefile_targets()
    assert not phony_only, (
        f".PHONY에만 있고 실제 recipe/dependency가 없는 target:\n  "
        + "\n  ".join(sorted(phony_only))
    )


# ── docs make 참조 → registry drift (확장 scan 범위) ──────────────────────

def test_docs_make_refs_in_registry():
    """README.md, START_HERE.md, docs/operations/**, docs/development/**, docs/release/**, scripts/README.md
    에 등장하는 make 참조가 모두 registry에 있어야 한다."""
    registry = _load_registry()
    registry_targets = {cmd["make_target"] for cmd in registry["commands"]}
    skip = {"help", "guide", "help-full", "help-json", "command-check"}

    scan_paths = _build_strict_scan_paths()
    refs = _collect_make_refs_from_paths(scan_paths)

    unregistered = [
        ref for ref in sorted(refs)
        if not re.search(r"[<>]", ref) and ref not in registry_targets and ref not in skip
    ]
    assert not unregistered, (
        "다음 docs 참조가 registry에 없습니다 (등록 또는 삭제 필요):\n  "
        + "\n  ".join(unregistered)
    )


def test_readme_make_refs_in_registry():
    """README.md, docs/START_HERE.md의 make 참조가 registry에 있어야 한다."""
    registry = _load_registry()
    registry_targets = {cmd["make_target"] for cmd in registry["commands"]}

    docs_to_scan = [ROOT / "README.md", ROOT / "docs" / "START_HERE.md"]
    pattern = re.compile(r"(?<![`\w])make\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)(?:\s|$|`|'|\")")

    unregistered: list[str] = []
    for path in docs_to_scan:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            target = m.group(1)
            if re.search(r"[<>]", target):
                continue
            if target not in registry_targets:
                unregistered.append(f"{path.relative_to(ROOT)}: make {target}")

    assert not unregistered, (
        "다음 docs 참조가 registry에 없습니다 (등록 또는 삭제 필요):\n  "
        + "\n  ".join(sorted(set(unregistered)))
    )


# ── command_registry.yaml의 make_target이 실제 Makefile에 존재 ────────────

def test_registry_targets_exist_in_makefile():
    registry = _load_registry()
    defined_targets, _ = _parse_makefile_targets()
    missing = [
        cmd["make_target"]
        for cmd in registry["commands"]
        if cmd["make_target"] not in defined_targets
    ]
    assert not missing, (
        "registry에 있지만 Makefile에 정의가 없는 target:\n  " + "\n  ".join(missing)
    )


# ── allow_unregistered_make_targets 검증 ──────────────────────────────────

def test_allow_unregistered_targets_exist_in_makefile():
    """allowlist의 target이 실제 Makefile에 존재해야 한다 (stale entry 방지)."""
    registry = _load_registry()
    defined_targets, _ = _parse_makefile_targets()
    allow_list = registry.get("allow_unregistered_make_targets", [])

    stale = [
        entry["target"]
        for entry in allow_list
        if entry.get("target") and entry["target"] not in defined_targets
    ]
    assert not stale, (
        "allowlist에 있지만 Makefile에 target이 없음 (stale entry):\n  "
        + "\n  ".join(stale)
    )


def test_allow_unregistered_targets_have_owner_reason():
    """allowlist의 모든 항목에 owner와 reason이 있어야 한다."""
    registry = _load_registry()
    allow_list = registry.get("allow_unregistered_make_targets", [])

    incomplete = [
        entry.get("target", "<unknown>")
        for entry in allow_list
        if not entry.get("owner") or not entry.get("reason")
    ]
    assert not incomplete, (
        "allowlist 항목에 owner 또는 reason이 없음:\n  " + "\n  ".join(incomplete)
    )


# ── public deletes_files 명령 metadata 검사 ──────────────────────────────

def test_public_deletes_files_has_metadata():
    """public deletes_files 명령은 docs, tests, writes_files 중 하나 이상이 있어야 한다."""
    registry = _load_registry()
    missing_meta = []
    for cmd in registry["commands"]:
        if cmd.get("safety") == "deletes_files" and cmd.get("kind") == "public":
            has_docs = bool(cmd.get("docs"))
            has_tests = bool(cmd.get("tests"))
            has_writes = bool(cmd.get("writes_files"))
            if not (has_docs or has_tests or has_writes):
                missing_meta.append(cmd["make_target"])
    assert not missing_meta, (
        "public deletes_files 명령에 docs/tests/writes_files가 모두 비어 있음:\n  "
        + "\n  ".join(missing_meta)
    )


# ── alias 명령 canonical 검사 ────────────────────────────────────────────

def test_alias_commands_have_canonical():
    """alias 명령은 canonical 또는 depends_on이 있어야 한다."""
    registry = _load_registry()
    missing_canonical = [
        cmd["make_target"]
        for cmd in registry["commands"]
        if cmd.get("kind") == "alias" and not cmd.get("canonical") and not cmd.get("depends_on")
    ]
    assert not missing_canonical, (
        "alias 명령에 canonical/depends_on이 없음:\n  " + "\n  ".join(missing_canonical)
    )


# ── internal/allowlisted 명령이 compact help에 노출되지 않음 ────────────

def test_internal_commands_not_in_compact_help():
    """internal 명령은 make help (compact) 출력에 나타나면 안 된다."""
    registry = _load_registry()
    internal_targets = [
        cmd["make_target"] for cmd in registry["commands"] if cmd.get("kind") == "internal"
    ]
    if not internal_targets:
        pytest.skip("internal 명령이 없음")

    result = subprocess.run(
        ["make", "help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHON": PYTHON},
    )
    assert result.returncode == 0
    output = result.stdout
    exposed = [t for t in internal_targets if t in output]
    assert not exposed, f"internal 명령이 compact help에 노출됨: {exposed}"
