from __future__ import annotations

import re

from document_test_helpers import EXAMPLES_DOC, ROOT, read_text


ACTIVE_DOC_ROOTS = [
    ROOT / "README.md",
    ROOT / "docs/README.md",
    ROOT / "docs/START_HERE.md",
    ROOT / "docs/development",
    ROOT / "docs/examples",
    ROOT / "docs/governance",
    ROOT / "docs/models",
    ROOT / "docs/operations",
    ROOT / "docs/release",
    ROOT / "docs/specs",
]


def _active_markdown_files():
    for root in ACTIVE_DOC_ROOTS:
        if root.is_file():
            yield root
        elif root.exists():
            yield from root.rglob("*.md")


def test_retired_siren_is_not_documented_as_active_example() -> None:
    text = EXAMPLES_DOC.read_text(encoding="utf-8")
    forbidden_patterns = [
        r"^##\s+Siren Detector 검증",
        r"^###\s+Siren Detector 검증",
        r"^##\s+통합 평가.*Siren.*동시",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text, re.MULTILINE), (
            f"docs/examples/requests.md contains retired Siren active section: {pattern}"
        )
    if "/v1/risk/detectors/siren/assessments" in text:
        assert "127.0.0.1:9405/v1/risk/detectors/siren/assessments" not in text


def test_removed_colbert_runtime_and_token_embedding_endpoint_do_not_return_as_active_contract() -> None:
    active_sources = [
        ROOT / "src/ai_model_serving/api/endpoint_spec.py",
        ROOT / "src/ai_model_serving/api/routers/gateway_retrieval.py",
        ROOT / "specs/openapi.gateway.yaml",
        ROOT / "specs/schemas/retrieval_score_request.schema.json",
        ROOT / "specs/schemas/retrieval_rerank_request.schema.json",
    ]
    forbidden = ["local-colbert-ko", "late_interaction_maxsim", "/v1/retrieval/token-embeddings"]
    for path in active_sources:
        lines = path.read_text(encoding="utf-8").splitlines()
        for marker in forbidden:
            active_mentions = [
                line
                for line in lines
                if marker in line and "removed" not in line and "제거" not in line
            ]
            assert not active_mentions, f"{path.relative_to(ROOT)} contains retired active marker {marker}"


def test_active_docs_do_not_reference_removed_build_artifacts() -> None:
    forbidden = [
        "Dockerfile.embedding-ko-vllm",
        "make rebuild-embedding-ko-vllm",
        "make build-embedding-ko-vllm-image",
    ]
    for path in _active_markdown_files():
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"{path.relative_to(ROOT)} references removed artifact: {marker}"


def test_legacy_document_locations_do_not_return() -> None:
    assert not (ROOT / "adr").exists()
    assert not (ROOT / "examples/requests/README.md").exists()
    for path in _active_markdown_files():
        text = path.read_text(encoding="utf-8")
        assert "examples/requests/README.md" not in text
        assert "](../adr/" not in text


def test_release_critical_local_embed_ko_markers_remain_in_active_docs() -> None:
    checks = {
        "docs/specs/api_docs_reference.md": ["local-embed-ko", "request_parameters"],
        "docs/operations/first_project_guide.md": ["embedding-ko-vllm:9406"],
        "docs/operations/monitoring_ux.md": ["local-embed-ko", "embedding-ko-vllm:9406"],
    }
    for rel, markers in checks.items():
        text = read_text(rel)
        for marker in markers:
            assert marker in text, f"{rel} missing release-critical marker: {marker}"


def test_root_changelog_does_not_become_maintenance_journal_again() -> None:
    changelog = read_text("CHANGELOG.md")
    for marker in ["Phase ", "phase ", "재감사", "maintenance journal"]:
        assert marker not in changelog, f"CHANGELOG.md contains maintenance-journal marker: {marker}"
