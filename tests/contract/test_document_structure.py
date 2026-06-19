from __future__ import annotations

import re
from pathlib import Path

from document_test_helpers import ADR_DIR, EXAMPLES_DOC, ROOT, load_manifest, stable_report_markdown_files


def test_canonical_document_homes_exist_without_legacy_roots() -> None:
    assert ADR_DIR.is_dir()
    assert (ADR_DIR / "README.md").is_file()
    assert EXAMPLES_DOC.is_file()
    assert not (ROOT / "adr").exists(), "root adr/ must not exist; use docs/adr/ instead."
    assert not (ROOT / "examples/requests/README.md").exists()


def test_root_examples_directory_requires_executable_artifacts() -> None:
    examples_dir = ROOT / "examples"
    if not examples_dir.exists():
        return

    files = [path for path in examples_dir.rglob("*") if path.is_file()]
    assert files, "root examples/ exists but has no executable sample payload/script"
    assert not [path for path in files if path.name.lower() == "readme.md"], (
        "descriptive examples README belongs in docs/examples/requests.md"
    )
    assert any(path.suffix in {".json", ".sh", ".py", ".http"} for path in files), (
        "root examples/ must contain actual payload/script artifacts"
    )


def test_empty_retrieval_package_not_reintroduced() -> None:
    retrieval_dir = ROOT / "src/ai_model_serving/retrieval"
    assert not retrieval_dir.exists(), "src/ai_model_serving/retrieval/ must not exist as an empty package"
    removed_import = "ai_model_serving" + ".retrieval"
    for base in ["src", "tests", "scripts"]:
        for path in (ROOT / base).rglob("*.py"):
            assert removed_import not in path.read_text(encoding="utf-8"), (
                f"{path.relative_to(ROOT)} imports removed retrieval package"
            )


def test_reports_refactor_current_contains_only_current_state_files() -> None:
    forbidden = {
        "current_end_to_end_flow_audit.md",
        "current_feature_ux_full_audit.md",
        "current_first_run_clean_package_audit.md",
        "current_full_file_hygiene_audit.md",
    }
    remaining = {path.name for path in (ROOT / "reports/refactor").glob("current_*.md")}
    assert not (remaining & forbidden), f"historical audit snapshots remain active: {sorted(remaining & forbidden)}"


def test_manifest_covers_managed_markdown_and_declares_root_entrypoints() -> None:
    manifest = load_manifest()
    registered = {entry["path"] for entry in manifest.get("documents", [])}
    docs_files = {
        str(path.relative_to(ROOT))
        for path in (ROOT / "docs").rglob("*.md")
        if path.name != "manifest.yaml"
    }
    managed_files = docs_files | stable_report_markdown_files() | {"README.md", "CHANGELOG.md"}
    assert not sorted(managed_files - registered), "docs/manifest.yaml is missing managed markdown files"
    assert {"README.md", "CHANGELOG.md"} <= registered


def test_manifest_schema_lifecycle_disposition_and_paths_are_valid() -> None:
    manifest = load_manifest()
    valid_lifecycles = set(manifest.get("lifecycle", {}).get("allowed", []))
    valid_dispositions = set(manifest.get("disposition", {}).get("allowed", []))

    assert manifest.get("schema_version") == 1
    assert valid_lifecycles == {"active", "reference", "generated", "historical", "deprecated"}
    assert valid_dispositions == {"keep", "rename", "consolidate", "archive", "delete", "generated"}

    errors: list[str] = []
    for entry in manifest.get("documents", []):
        path = entry.get("path", "<missing-path>")
        if not (ROOT / path).exists() and entry.get("lifecycle") != "generated":
            errors.append(f"{path}: registered path does not exist")
        if not entry.get("type"):
            errors.append(f"{path}: missing type")
        if not entry.get("owner"):
            errors.append(f"{path}: missing owner")
        if entry.get("lifecycle") not in valid_lifecycles:
            errors.append(f"{path}: invalid lifecycle {entry.get('lifecycle')!r}")
        if entry.get("disposition") is not None and entry.get("disposition") not in valid_dispositions:
            errors.append(f"{path}: invalid disposition {entry.get('disposition')!r}")
    assert not errors, f"invalid manifest entries: {errors}"


def test_manifest_lifecycle_specific_policy_fields() -> None:
    manifest = load_manifest()
    reference_missing = [
        entry["path"]
        for entry in manifest.get("documents", [])
        if entry.get("lifecycle") == "reference"
        and not (entry.get("source_of_truth") or entry.get("verified_by"))
    ]
    generated_missing_generator = [
        entry["path"]
        for entry in manifest.get("documents", [])
        if entry.get("lifecycle") == "generated" and not entry.get("generator")
    ]
    generated_missing_do_not_edit = [
        entry["path"]
        for entry in manifest.get("documents", [])
        if entry.get("lifecycle") == "generated" and entry.get("do_not_edit") is not True
    ]
    archive_wrong = [
        entry["path"]
        for entry in manifest.get("documents", [])
        if "/archive/" in entry["path"] and entry.get("lifecycle") != "historical"
    ]

    assert not reference_missing, f"reference documents without source_of_truth or verified_by: {reference_missing}"
    assert not generated_missing_generator, f"generated documents without generator: {generated_missing_generator}"
    assert not generated_missing_do_not_edit, f"generated documents without do_not_edit: true: {generated_missing_do_not_edit}"
    assert not archive_wrong, f"archive documents must be historical in manifest: {archive_wrong}"


def test_manifest_verified_by_paths_use_current_document_test_suite() -> None:
    manifest = load_manifest()
    verified_paths = {
        path
        for entry in manifest.get("documents", [])
        for path in entry.get("verified_by", [])
        if path.startswith("tests/")
    }
    assert "tests/contract/test_document_governance.py" not in verified_paths
    for rel in verified_paths:
        assert (ROOT / rel).exists(), f"manifest verified_by path does not exist: {rel}"


def test_active_review_workplan_ux_docs_have_disposition() -> None:
    manifest = load_manifest()
    offenders = []
    for entry in manifest.get("documents", []):
        path = entry["path"]
        if entry.get("lifecycle") != "active" or not path.startswith("docs/operations/"):
            continue
        name = Path(path).name
        if any(marker in name for marker in ("_review.md", "_workplan.md", "_ux.md")) and not entry.get("disposition"):
            offenders.append(path)
    assert not offenders, f"active review/workplan/ux docs need explicit disposition: {offenders}"


def test_entrypoints_do_not_reference_legacy_doc_paths() -> None:
    forbidden = ["../adr/", "](../adr/", "examples/requests/README.md"]
    for path in [ROOT / "docs/README.md", ROOT / "docs/START_HERE.md", ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in text, f"{path.relative_to(ROOT)} references legacy path {phrase}"


def test_active_guides_do_not_promote_archive_as_source_of_truth() -> None:
    manifest = load_manifest()
    active_paths = [
        ROOT / entry["path"]
        for entry in manifest.get("documents", [])
        if entry.get("lifecycle") in {"active", "reference"} and entry["path"].endswith(".md")
    ]
    pattern = re.compile(r"source-of-truth[^\n]*(docs/archive/|reports/archive/)|(docs/archive/|reports/archive/)[^\n]*source-of-truth")
    for path in active_paths:
        assert not pattern.search(path.read_text(encoding="utf-8")), (
            f"{path.relative_to(ROOT)} promotes archive content as source-of-truth"
        )


def test_change_impact_matrix_and_pr_checklist_cover_document_governance() -> None:
    policy = (ROOT / "docs/governance/document_management.md").read_text(encoding="utf-8")
    template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")

    for phrase in ["docs structure change", "docs/manifest.yaml", ".github/pull_request_template.md"]:
        assert phrase in policy, f"document_management.md missing change-impact phrase: {phrase}"
    assert "docs/manifest.yaml" in template
    assert "generated docs/reports" in template
