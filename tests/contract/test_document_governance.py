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

# ── A. examples risk code 검증 ────────────────────────────────────────────────

def _load_taxonomy_codes() -> set[str]:
    taxonomy = yaml.safe_load((ROOT / "configs/risk_taxonomy.yaml").read_text(encoding="utf-8"))
    codes: set[str] = set()
    for family in taxonomy.get("families", {}).values():
        codes.update(family.get("codes", {}).keys())
    return codes


def test_examples_risk_codes_exist_in_taxonomy() -> None:
    """examples/requests/README.md에서 heading으로 쓰인 A/I code가 taxonomy에 존재해야 한다."""
    allowed = _load_taxonomy_codes()
    text = (ROOT / "examples/requests/README.md").read_text(encoding="utf-8")
    # ### A1 — ... 또는 ### I2 — ... 형태의 heading만 검사
    pattern = re.compile(r"^###\s+([AI]\d+)\s*[—–-]", re.MULTILINE)
    found_codes = pattern.findall(text)
    for code in found_codes:
        assert code in allowed, (
            f"examples/requests/README.md uses risk code '{code}' which is not in "
            f"configs/risk_taxonomy.yaml. Allowed: {sorted(allowed)}"
        )


def test_examples_no_invalid_risk_codes() -> None:
    """A3, A4, A5, I5는 taxonomy에 없으므로 active heading으로 사용하지 않는다."""
    invalid = {"A3", "A4", "A5", "I5"}
    text = (ROOT / "examples/requests/README.md").read_text(encoding="utf-8")
    pattern = re.compile(r"^###\s+([AI]\d+)\s*[—–-]", re.MULTILINE)
    found = set(pattern.findall(text))
    bad = found & invalid
    assert not bad, (
        f"examples/requests/README.md uses invalid risk codes as headings: {sorted(bad)}"
    )


# ── B. retired Siren active example 금지 ─────────────────────────────────────

def test_examples_siren_not_active() -> None:
    """Siren endpoint가 active detector로 문서화되지 않는다."""
    text = (ROOT / "examples/requests/README.md").read_text(encoding="utf-8")
    # "Siren Detector 검증" 같은 active section heading 금지
    forbidden_patterns = [
        r"^##\s+Siren Detector 검증",
        r"^###\s+Siren Detector 검증",
        r"^##\s+통합 평가.*Siren.*동시",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text, re.MULTILINE), (
            f"examples/requests/README.md contains retired Siren active section: {pattern}"
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
    """adr/*.md (README 제외)가 docs/02_decision_register.md에 모두 index되어 있어야 한다."""
    adr_dir = ROOT / "adr"
    index_text = (ROOT / "docs/02_decision_register.md").read_text(encoding="utf-8")
    for adr_file in sorted(adr_dir.glob("[0-9]*.md")):
        stem = adr_file.stem  # e.g. "0010-colbert-removal-..."
        assert stem in index_text, (
            f"adr/{adr_file.name} is not indexed in docs/02_decision_register.md"
        )


# ── G. ADR status 검증 ───────────────────────────────────────────────────────

_VALID_STATUSES = {"Proposed", "Accepted", "Deprecated", "Rejected"}
_SUPERSEDED_RE = re.compile(r"^Superseded by ADR-\d+", re.MULTILINE)


def test_adr_files_have_valid_status() -> None:
    """각 ADR에 Status 섹션이 있고 유효한 status여야 한다."""
    adr_dir = ROOT / "adr"
    for adr_file in sorted(adr_dir.glob("[0-9]*.md")):
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
