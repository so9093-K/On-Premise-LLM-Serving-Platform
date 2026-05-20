"""
문서 governance 계약 테스트.

source-of-truth(risk_taxonomy, model_cards, Makefile 등)와
examples/docs의 일치를 검증한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs/adr"
EXAMPLES_DOC = ROOT / "docs/examples/requests.md"


def _load_manifest() -> dict:
    return yaml.safe_load((ROOT / "docs/manifest.yaml").read_text(encoding="utf-8"))


def _stable_report_markdown_files() -> set[str]:
    return {
        str(path.relative_to(ROOT))
        for path in (ROOT / "reports").rglob("*.md")
        if not re.search(r"runtime_validation_\d{8}T\d{6}Z\.md$", path.name)
    }

# ── A. examples risk code 검증 ────────────────────────────────────────────────

def _load_taxonomy_codes() -> set[str]:
    taxonomy = yaml.safe_load((ROOT / "configs/risk_taxonomy.yaml").read_text(encoding="utf-8"))
    codes: set[str] = set()
    for family in taxonomy.get("families", {}).values():
        codes.update(family.get("codes", {}).keys())
    return codes


def test_examples_risk_codes_exist_in_taxonomy() -> None:
    """docs/examples/requests.md에서 heading으로 쓰인 A/I code가 taxonomy에 존재해야 한다."""
    allowed = _load_taxonomy_codes()
    text = EXAMPLES_DOC.read_text(encoding="utf-8")
    # ### A1 — ... 또는 ### I2 — ... 형태의 heading만 검사
    pattern = re.compile(r"^###\s+([AI]\d+)\s*[—–-]", re.MULTILINE)
    found_codes = pattern.findall(text)
    for code in found_codes:
        assert code in allowed, (
            f"{EXAMPLES_DOC.relative_to(ROOT)} uses risk code '{code}' which is not in "
            f"configs/risk_taxonomy.yaml. Allowed: {sorted(allowed)}"
        )


def test_examples_no_invalid_risk_codes() -> None:
    """A3, A4, A5, I5는 taxonomy에 없으므로 active heading으로 사용하지 않는다."""
    invalid = {"A3", "A4", "A5", "I5"}
    text = EXAMPLES_DOC.read_text(encoding="utf-8")
    pattern = re.compile(r"^###\s+([AI]\d+)\s*[—–-]", re.MULTILINE)
    found = set(pattern.findall(text))
    bad = found & invalid
    assert not bad, (
        f"docs/examples/requests.md uses invalid risk codes as headings: {sorted(bad)}"
    )


# ── B. retired Siren active example 금지 ─────────────────────────────────────

def test_examples_siren_not_active() -> None:
    """Siren endpoint가 active detector로 문서화되지 않는다."""
    text = EXAMPLES_DOC.read_text(encoding="utf-8")
    # "Siren Detector 검증" 같은 active section heading 금지
    forbidden_patterns = [
        r"^##\s+Siren Detector 검증",
        r"^###\s+Siren Detector 검증",
        r"^##\s+통합 평가.*Siren.*동시",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text, re.MULTILINE), (
            f"docs/examples/requests.md contains retired Siren active section: {pattern}"
        )


# ── C. 없는 build artifact 참조 금지 ─────────────────────────────────────────

_DOCS_GLOB = list((ROOT / "docs").rglob("*.md")) + [ROOT / "README.md"]

def test_docs_no_nonexistent_embedding_ko_dockerfile() -> None:
    """Dockerfile.embedding-ko-vllm은 존재하지 않으므로 docs에서 참조하지 않는다."""
    for path in _DOCS_GLOB:
        text = path.read_text(encoding="utf-8")
        assert "Dockerfile.embedding-ko-vllm" not in text, (
            f"{path.relative_to(ROOT)} references non-existent Dockerfile.embedding-ko-vllm"
        )


def test_docs_no_nonexistent_embedding_ko_make_targets() -> None:
    """make rebuild-embedding-ko-vllm / make build-embedding-ko-vllm-image는 존재하지 않는다."""
    nonexistent = ["rebuild-embedding-ko-vllm", "build-embedding-ko-vllm-image"]
    for path in _DOCS_GLOB:
        text = path.read_text(encoding="utf-8")
        for target in nonexistent:
            assert target not in text, (
                f"{path.relative_to(ROOT)} references non-existent make target: {target}"
            )


# ── D. model_cards 문서 일치 ─────────────────────────────────────────────────

def test_model_cards_doc_includes_all_active_cards() -> None:
    """docs/models/model_cards.md에 model_cards/*.json의 모든 logical_id가 등장해야 한다."""
    import json

    card_dir = ROOT / "model_cards"
    active_ids: list[str] = []
    for card_file in sorted(card_dir.glob("*.json")):
        data = json.loads(card_file.read_text(encoding="utf-8"))
        active_ids.append(data["logical_id"])

    doc = (ROOT / "docs/models/model_cards.md").read_text(encoding="utf-8")
    for logical_id in active_ids:
        assert logical_id in doc, (
            f"docs/models/model_cards.md is missing model id '{logical_id}' "
            f"from model_cards/{logical_id}.json"
        )


def test_model_cards_doc_no_risk_siren_retained_claim() -> None:
    """risk-siren이 'retained' 또는 'lineage' 목적으로 남아 있다는 거짓 문장을 포함하지 않는다."""
    doc = (ROOT / "docs/models/model_cards.md").read_text(encoding="utf-8")
    forbidden_phrases = [
        "risk-siren 모델 카드는 lineage",
        "risk-siren model card",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in doc, (
            f"docs/models/model_cards.md contains false risk-siren retained claim: '{phrase}'"
        )


# ── E. endpoint reference drift 방지 ─────────────────────────────────────────

def test_endpoint_reference_no_local_embed_only_claim() -> None:
    """/v1/embeddings를 local-embed 전용이라고 설명하지 않는다."""
    text = (ROOT / "docs/operations/endpoint_reference.md").read_text(encoding="utf-8")
    forbidden = [
        "local-embed` dense embedding 전용",
        "local-embed 전용 OpenAI",
    ]
    for phrase in forbidden:
        assert phrase not in text, (
            f"docs/operations/endpoint_reference.md claims /v1/embeddings is local-embed only: '{phrase}'"
        )


def test_endpoint_reference_has_local_embed_ko_prompt_policy() -> None:
    """endpoint_reference.md에 local-embed-ko prompt policy 설명이 있어야 한다."""
    text = (ROOT / "docs/operations/endpoint_reference.md").read_text(encoding="utf-8")
    assert "query: " in text, (
        "docs/operations/endpoint_reference.md must describe local-embed-ko 'query: ' prefix prompt policy"
    )
    assert "local-embed-ko" in text, (
        "docs/operations/endpoint_reference.md must reference local-embed-ko"
    )


# ── F. ADR index 검증 ─────────────────────────────────────────────────────────

def test_adr_files_indexed_in_decision_register() -> None:
    """docs/adr/*.md (README 제외)가 ADR index에 모두 index되어 있어야 한다."""
    index_text = (ROOT / "docs/02_decision_register.md").read_text(encoding="utf-8")
    readme_text = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    for adr_file in sorted(ADR_DIR.glob("[0-9]*.md")):
        stem = adr_file.stem  # e.g. "0010-colbert-removal-..."
        assert stem in index_text or stem in readme_text, (
            f"docs/adr/{adr_file.name} is not indexed in docs/02_decision_register.md "
            "or docs/adr/README.md"
        )


# ── G. ADR status 검증 ───────────────────────────────────────────────────────

_VALID_STATUSES = {"Proposed", "Accepted", "Deprecated", "Rejected"}
_SUPERSEDED_RE = re.compile(r"^Superseded by ADR-\d+", re.MULTILINE)


def test_adr_files_have_valid_status() -> None:
    """각 ADR에 Status 섹션이 있고 유효한 status여야 한다."""
    for adr_file in sorted(ADR_DIR.glob("[0-9]*.md")):
        text = adr_file.read_text(encoding="utf-8")
        assert "## Status" in text, f"{adr_file.name} is missing ## Status section"
        # Status 섹션 내용 추출
        status_match = re.search(r"## Status\s*\n+(.+)", text)
        assert status_match, f"{adr_file.name} ## Status section has no content"
        status_line = status_match.group(1).strip()
        valid = (
            status_line in _VALID_STATUSES
            or bool(_SUPERSEDED_RE.match(status_line))
        )
        assert valid, (
            f"{adr_file.name} has invalid status: '{status_line}'. "
            f"Allowed: {sorted(_VALID_STATUSES)} or 'Superseded by ADR-XXXX'"
        )


# ── H. retrieval schema enum 검증 ────────────────────────────────────────────

def test_retrieval_schema_score_mode_enum_has_no_late_interaction() -> None:
    """retrieval 요청 schema의 score_mode enum에 late_interaction_maxsim이 없어야 한다."""
    import json

    for schema_file in ["retrieval_score_request.schema.json", "retrieval_rerank_request.schema.json"]:
        data = json.loads((ROOT / "specs/schemas" / schema_file).read_text(encoding="utf-8"))
        text = json.dumps(data)
        assert "late_interaction_maxsim" not in text, (
            f"specs/schemas/{schema_file} still contains 'late_interaction_maxsim' in enum. "
            "dense_cosine is the only supported score_mode."
        )


def test_retrieval_schema_model_enum_has_no_local_colbert() -> None:
    """retrieval 요청 schema의 model enum에 local-colbert-ko가 없어야 한다."""
    import json

    for schema_file in ["retrieval_score_request.schema.json", "retrieval_rerank_request.schema.json"]:
        data = json.loads((ROOT / "specs/schemas" / schema_file).read_text(encoding="utf-8"))
        text = json.dumps(data)
        assert "local-colbert-ko" not in text, (
            f"specs/schemas/{schema_file} still references 'local-colbert-ko'. "
            "Active retrieval models are local-embed-ko and local-embed only."
        )


# ── I. 추가 drift 방지 ────────────────────────────────────────────────────────

def test_examples_retired_siren_uses_gateway_port() -> None:
    """retired Siren curl 예시가 있다면 Gateway 9400 포트를 사용해야 한다 (Risk Adapter 9405 금지)."""
    text = EXAMPLES_DOC.read_text(encoding="utf-8")
    if "/v1/risk/detectors/siren/assessments" in text:
        assert "127.0.0.1:9405/v1/risk/detectors/siren/assessments" not in text, (
            "docs/examples/requests.md references Siren endpoint on Risk Adapter port 9405. "
            "The 410 Gone compatibility route is served by Gateway at port 9400."
        )


def test_adr_0003_superseded_if_siren_present() -> None:
    """ADR-0003에 Siren 언급이 있으면 Superseded 처리되어야 한다."""
    text = (ADR_DIR / "0003-all-vllm-runtime.md").read_text(encoding="utf-8")
    if "Siren" in text or "siren" in text:
        assert "Superseded by ADR-0010" in text, (
            "docs/adr/0003-all-vllm-runtime.md mentions Siren but is not marked "
            "Superseded by ADR-0010."
        )


def test_api_docs_embeddings_not_local_embed_only() -> None:
    """/v1/embeddings를 local-embed 전용으로 설명하지 않는다."""
    text = (ROOT / "docs/specs/api.md").read_text(encoding="utf-8")
    forbidden = [
        "POST /v1/embeddings` | `local-embed` embedding",
        "POST /v1/embeddings | local-embed embedding",
    ]
    for phrase in forbidden:
        assert phrase not in text, (
            f"docs/specs/api.md claims /v1/embeddings is local-embed only: '{phrase}'. "
            "/v1/embeddings supports both local-embed and local-embed-ko."
        )
    assert "local-embed-ko" in text, (
        "docs/specs/api.md must mention local-embed-ko."
    )


def test_endpoint_reference_request_parameters_match_embedding_projection() -> None:
    """`endpoint_reference.md`의 /v1/models parameter 표가 실제 projection과 일치해야 한다."""
    text = (ROOT / "docs/operations/endpoint_reference.md").read_text(encoding="utf-8")
    # user는 projection에 없으므로 조정 가능 칸에 없어야 한다
    assert (
        "`local-embed-ko` | `encoding_format`, `truncate_prompt_tokens`(-1 또는 1–2048), `user`" not in text
    ), (
        "docs/operations/endpoint_reference.md lists 'user' as adjustable for local-embed-ko. "
        "user is NOT in _embedding_request_parameters() projection."
    )
    # dimensions는 local-embed-ko에서 조정 가능 칸(enum:[1024])에 있어야 한다
    assert "`local-embed-ko` | `dimensions`(1024" in text, (
        "docs/operations/endpoint_reference.md must show 'dimensions'(1024만 허용) as adjustable for local-embed-ko."
    )
    # user 필드 정책 설명이 있어야 한다
    assert "user`는 embedding request schema에서 허용" in text, (
        "docs/operations/endpoint_reference.md must explain that 'user' is accepted by schema "
        "but not exposed in /v1/models request_parameters."
    )


# ── J. docs/adr/README.md index 검증 ────────────────────────────────────────

def test_adr_readme_index_matches_adr_statuses() -> None:
    """docs/adr/README.md 인덱스 표의 status가 각 ADR 파일의 실제 status와 일치해야 한다."""
    readme = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    for adr_file in sorted(ADR_DIR.glob("[0-9]*.md")):
        text = adr_file.read_text(encoding="utf-8")
        status_match = re.search(r"## Status\s*\n+(.+)", text)
        assert status_match, f"{adr_file.name} is missing ## Status section"
        status = status_match.group(1).strip()
        adr_id = f"ADR-{adr_file.name[:4]}"
        assert adr_id in readme, (
            f"docs/adr/README.md does not include {adr_id} in its index."
        )
        assert status in readme, (
            f"docs/adr/README.md does not reflect {adr_id} status: '{status}'. "
            "Update the ADR index table to match the actual ADR file."
        )


def test_adr_readme_does_not_claim_decision_register_is_canonical() -> None:
    """docs/adr/README.md가 docs/02_decision_register.md를 canonical로 주장하지 않아야 한다."""
    text = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    assert "docs/02_decision_register.md를 기준으로 한다" not in text, (
        "docs/adr/README.md incorrectly states that docs/02_decision_register.md is the canonical record. "
        "docs/adr/ files are canonical; docs/02_decision_register.md is the index."
    )
    assert "canonical" in text or "source-of-truth" in text, (
        "docs/adr/README.md must declare docs/adr/ directory as the canonical decision record."
    )


def test_root_adr_directory_is_not_reintroduced() -> None:
    """root adr/는 더 이상 문서 홈이 아니므로 다시 생기면 실패한다."""
    assert not (ROOT / "adr").exists(), "root adr/ must not exist; use docs/adr/ instead."


def test_docs_entrypoints_do_not_reference_legacy_doc_paths() -> None:
    """문서 진입점이 옛 ADR/examples 경로를 다시 안내하지 않아야 한다."""
    forbidden = ["../adr/", "](../adr/", "examples/requests/README.md"]
    for path in [ROOT / "docs/README.md", ROOT / "docs/START_HERE.md", ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in text, f"{path.relative_to(ROOT)} references legacy path {phrase}"


def test_changelog_latest_release_matches_version() -> None:
    """CHANGELOG 최신 release heading은 VERSION과 충돌하지 않아야 한다."""
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## \[?([0-9]+\.[0-9]+\.[0-9][^\]\s]*)\]?", text, re.MULTILINE)
    assert headings, "CHANGELOG.md must include at least one release heading"
    assert headings[0] == version, (
        f"CHANGELOG.md latest release heading {headings[0]!r} does not match VERSION {version!r}"
    )


def test_changelog_is_not_maintenance_journal() -> None:
    """root CHANGELOG는 긴 phase/maintenance journal이 아니라 릴리스 노트여야 한다."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    forbidden = ["Phase ", "phase ", "재감사", "maintenance journal"]
    for phrase in forbidden:
        assert phrase not in text, f"CHANGELOG.md contains maintenance-journal marker: {phrase}"
    assert text.count("0.1.0-rc.1") <= 2, "CHANGELOG.md should only mention archived rc journal as a pointer"
    assert len(text.splitlines()) < 90, "CHANGELOG.md should stay short; archive long journals under docs/archive/changelog/"


def test_reports_refactor_current_does_not_contain_audit_snapshots() -> None:
    """reports/refactor/current_*에는 실제 current handoff/state/inventory만 남긴다."""
    forbidden = {
        "current_end_to_end_flow_audit.md",
        "current_feature_ux_full_audit.md",
        "current_first_run_clean_package_audit.md",
        "current_full_file_hygiene_audit.md",
    }
    remaining = {path.name for path in (ROOT / "reports/refactor").glob("current_*.md")}
    assert not (remaining & forbidden), f"historical audit snapshots remain active: {sorted(remaining & forbidden)}"


def test_docs_manifest_covers_managed_markdown() -> None:
    """docs/manifest.yaml에 없는 managed docs/reports markdown은 active tree에 둘 수 없다."""
    manifest = _load_manifest()
    registered = {entry["path"] for entry in manifest.get("documents", [])}
    docs_files = {
        str(path.relative_to(ROOT))
        for path in (ROOT / "docs").rglob("*.md")
        if path.name != "manifest.yaml"
    }
    managed_files = docs_files | _stable_report_markdown_files() | {"README.md", "CHANGELOG.md"}
    missing = sorted(managed_files - registered)
    assert not missing, f"docs/manifest.yaml is missing managed markdown files: {missing}"


def test_manifest_declares_schema_and_lifecycle_policy() -> None:
    """manifest 자체가 schema version과 lifecycle 허용 목록을 선언해야 한다."""
    manifest = _load_manifest()
    assert manifest.get("schema_version") == 1
    assert set(manifest.get("lifecycle", {}).get("allowed", [])) == {
        "active",
        "reference",
        "generated",
        "historical",
        "deprecated",
    }


def test_manifest_registered_paths_exist() -> None:
    """manifest에 등록된 문서 파일은 실제로 존재해야 한다."""
    manifest = _load_manifest()
    missing = [
        entry["path"]
        for entry in manifest.get("documents", [])
        if not (ROOT / entry["path"]).exists()
    ]
    assert not missing, f"docs/manifest.yaml registers missing files: {missing}"


def test_manifest_entries_have_required_fields_and_valid_lifecycle() -> None:
    """각 manifest 항목은 type/owner와 허용 lifecycle을 가져야 한다."""
    manifest = _load_manifest()
    valid_lifecycles = set(manifest.get("lifecycle", {}).get("allowed", []))
    errors: list[str] = []
    for entry in manifest.get("documents", []):
        path = entry.get("path", "<missing-path>")
        if not entry.get("type"):
            errors.append(f"{path}: missing type")
        if not entry.get("owner"):
            errors.append(f"{path}: missing owner")
        lifecycle = entry.get("lifecycle")
        if lifecycle not in valid_lifecycles:
            errors.append(f"{path}: invalid lifecycle {lifecycle!r}")
    assert not errors, f"invalid manifest entries: {errors}"


def test_manifest_reference_docs_have_source_or_verification() -> None:
    """reference 문서는 source_of_truth 또는 verified_by를 명시해야 한다."""
    manifest = _load_manifest()
    missing = [
        entry["path"]
        for entry in manifest.get("documents", [])
        if entry.get("lifecycle") == "reference"
        and not (entry.get("source_of_truth") or entry.get("verified_by"))
    ]
    assert not missing, f"reference documents without source_of_truth or verified_by: {missing}"


def test_manifest_generated_docs_have_generator() -> None:
    """generated lifecycle 문서는 generator를 명시해야 한다."""
    manifest = _load_manifest()
    missing = [
        entry["path"]
        for entry in manifest.get("documents", [])
        if entry.get("lifecycle") == "generated" and not entry.get("generator")
    ]
    assert not missing, f"generated documents without generator: {missing}"


def test_manifest_generated_docs_are_marked_do_not_edit() -> None:
    """generated lifecycle 문서는 직접 편집 금지 정책을 가져야 한다."""
    manifest = _load_manifest()
    missing = [
        entry["path"]
        for entry in manifest.get("documents", [])
        if entry.get("lifecycle") == "generated" and entry.get("do_not_edit") is not True
    ]
    assert not missing, f"generated documents without do_not_edit: true: {missing}"


def test_archive_docs_are_historical_in_manifest() -> None:
    """archive 문서는 manifest에서 historical로 표시한다."""
    manifest = _load_manifest()
    by_path = {entry["path"]: entry for entry in manifest.get("documents", [])}
    archive_paths = [path for path in by_path if "/archive/" in path]
    wrong = [path for path in archive_paths if by_path[path].get("lifecycle") != "historical"]
    assert not wrong, f"archive documents must be historical in manifest: {wrong}"


def test_active_guides_do_not_promote_archive_as_source_of_truth() -> None:
    """active guide가 archive 문서를 현재 source-of-truth처럼 안내하지 않아야 한다."""
    manifest = _load_manifest()
    active_paths = [
        ROOT / entry["path"]
        for entry in manifest.get("documents", [])
        if entry.get("lifecycle") in {"active", "reference"} and entry["path"].endswith(".md")
    ]
    pattern = re.compile(r"source-of-truth[^\n]*(docs/archive/|reports/archive/)|(docs/archive/|reports/archive/)[^\n]*source-of-truth")
    for path in active_paths:
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), (
            f"{path.relative_to(ROOT)} promotes archive content as source-of-truth"
        )


def test_manifest_declares_root_entrypoints() -> None:
    """root README와 CHANGELOG도 문서 운영 registry에 포함한다."""
    manifest = _load_manifest()
    registered = {entry["path"] for entry in manifest.get("documents", [])}
    assert {"README.md", "CHANGELOG.md"} <= registered


def test_change_impact_matrix_includes_docs_structure_change() -> None:
    """문서 구조 변경도 impact matrix와 PR 흐름에 연결되어야 한다."""
    policy = (ROOT / "docs/governance/document_management.md").read_text(encoding="utf-8")
    required = ["docs structure change", "docs/manifest.yaml", ".github/pull_request_template.md"]
    for phrase in required:
        assert phrase in policy, f"document_management.md missing change-impact phrase: {phrase}"


def test_pr_template_requires_manifest_review() -> None:
    """PR checklist가 docs/manifest.yaml 갱신 여부를 묻는다."""
    template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    assert "docs/manifest.yaml" in template
    assert "generated docs/reports" in template


def test_model_parameter_doc_no_user_in_request_parameters_table() -> None:
    """/v1/models.request_parameters 표에 user가 없어야 한다.

    user는 embedding request schema에서 accept하지만 _embedding_request_parameters()가
    projection에 포함하지 않는다. 표에 넣으면 클라이언트가 /v1/models에서 user를 반환받을
    것으로 잘못 기대하게 된다.
    """
    text = (ROOT / "docs/operations/model_parameter_discovery.md").read_text(encoding="utf-8")
    forbidden = [
        "`local-embed` | `dimensions`, `encoding_format`, `truncate_prompt_tokens`, `user`",
        "`local-embed-ko` | `dimensions`(1024만 허용), `encoding_format`, `truncate_prompt_tokens`, `user`",
        "`dimensions`(1024 고정), `encoding_format`, `truncate_prompt_tokens`, `user`",
    ]
    for phrase in forbidden:
        assert phrase not in text, (
            f"docs/operations/model_parameter_discovery.md still lists 'user' in /v1/models "
            f"request_parameters table: '{phrase}'. "
            "user is accept/drop only and is not in the /v1/models projection."
        )
