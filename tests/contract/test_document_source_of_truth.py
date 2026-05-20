from __future__ import annotations

import json
import re

from document_test_helpers import ADR_DIR, EXAMPLES_DOC, ROOT, load_json, load_yaml, public_model_ids, read_text, runtime_service_ports


def test_examples_risk_codes_exist_in_taxonomy() -> None:
    taxonomy = load_yaml("configs/risk_taxonomy.yaml")
    allowed = {
        code
        for family in taxonomy.get("families", {}).values()
        for code in family.get("codes", {})
    }
    headings = re.findall(r"^###\s+([AI]\d+)\s*[—–-]", EXAMPLES_DOC.read_text(encoding="utf-8"), re.MULTILINE)
    assert headings
    assert not sorted(set(headings) - allowed), "docs/examples/requests.md uses risk codes absent from configs/risk_taxonomy.yaml"


def test_model_cards_reference_lists_public_model_cards() -> None:
    model_card_ids = {
        load_json(str(path.relative_to(ROOT)))["logical_id"]
        for path in (ROOT / "model_cards").glob("*.json")
    }
    expected = public_model_ids()
    doc = read_text("docs/models/model_cards.md")

    assert expected <= model_card_ids
    missing = sorted(model_id for model_id in expected if model_id not in doc)
    assert not missing, f"docs/models/model_cards.md missing public model ids: {missing}"


def test_endpoint_reference_embedding_policy_matches_model_serving() -> None:
    serving = load_yaml("configs/model_serving.yaml")
    local_embed_ko = serving["embedding_profiles"]["local-embed-ko"]
    text = read_text("docs/operations/endpoint_reference.md")

    assert local_embed_ko["prompt_policy"]["retrieval_query"]["prefix"] in text
    assert local_embed_ko["served_model_name"] in text
    assert f"`dimensions`({local_embed_ko['default_dimensions']}" in text
    assert "user`는 embedding request schema에서 허용" in text


def test_api_reference_and_api_summary_include_public_embedding_models() -> None:
    embedding_models = {
        model_id
        for model_id, model in load_yaml("configs/model_catalog.yaml")["models"].items()
        if model["primary_capability"] == "embeddings"
        and model.get("gateway_listing", {}).get("enabled") is True
    }
    docs = {
        "docs/specs/api.md": read_text("docs/specs/api.md"),
        "docs/specs/api_docs_reference.md": read_text("docs/specs/api_docs_reference.md"),
    }
    for rel, text in docs.items():
        missing = sorted(model_id for model_id in embedding_models if model_id not in text)
        assert not missing, f"{rel} missing public embedding models from catalog: {missing}"


def test_runtime_docs_match_model_serving_ports() -> None:
    expected = runtime_service_ports()
    docs = {
        "docs/operations/first_project_guide.md": read_text("docs/operations/first_project_guide.md"),
        "docs/operations/full_stack_runtime.md": read_text("docs/operations/full_stack_runtime.md"),
        "scripts/README.md": read_text("scripts/README.md"),
        "scripts/compose/preflight_compose.sh": read_text("scripts/compose/preflight_compose.sh"),
    }
    for service, port in expected.items():
        service_name = service.split(":", 1)[0]
        assert any(str(port) in text for text in docs.values()), f"runtime port {port} for {service} is absent from docs/scripts"
        if service_name == "embedding-ko-vllm":
            assert any(f"{service_name}:{port}" in text for text in docs.values()), (
                "embedding-ko-vllm runtime must be documented with its configured port"
            )


def test_monitoring_doc_model_table_matches_public_model_catalog() -> None:
    catalog = load_yaml("configs/model_catalog.yaml")["models"]
    text = read_text("docs/operations/monitoring_ux.md")
    missing = [
        model_id
        for model_id, model in catalog.items()
        if model.get("gateway_listing", {}).get("enabled") is True and model_id not in text
    ]
    assert not missing, f"monitoring_ux.md missing public model ids: {missing}"


def test_smoke_test_expected_models_match_public_catalog() -> None:
    smoke = read_text("scripts/ops/smoke_test.sh")
    missing = sorted(model_id for model_id in public_model_ids() if model_id not in smoke)
    assert not missing, f"scripts/ops/smoke_test.sh missing public model ids: {missing}"
    assert '"model":"local-embed-ko"' in smoke


def test_governance_model_config_validation_covers_catalog_models() -> None:
    text = read_text("src/ai_model_serving/governance_validation/model_config.py")
    assert "for logical_id in sorted(catalog)" in text
    assert "catalog['local-embed-ko']" in text
    assert "output_dimensions" in text


def test_adr_index_and_readme_match_adr_files() -> None:
    index_text = read_text("docs/02_decision_register.md")
    readme_text = read_text("docs/adr/README.md")

    for adr_file in sorted(ADR_DIR.glob("[0-9]*.md")):
        stem = adr_file.stem
        text = adr_file.read_text(encoding="utf-8")
        status_match = re.search(r"## Status\s*\n+(.+)", text)
        assert status_match, f"{adr_file.name} is missing ## Status section"
        status = status_match.group(1).strip()
        adr_id = f"ADR-{adr_file.name[:4]}"

        assert stem in index_text or stem in readme_text, f"{adr_file.name} is not indexed"
        assert adr_id in readme_text, f"docs/adr/README.md does not include {adr_id}"
        assert status in readme_text, f"docs/adr/README.md status for {adr_id} is stale"


def test_adr_status_policy_and_supersede_contract() -> None:
    valid_statuses = {"Proposed", "Accepted", "Deprecated", "Rejected"}
    superseded_re = re.compile(r"^Superseded by ADR-\d+")
    for adr_file in sorted(ADR_DIR.glob("[0-9]*.md")):
        text = adr_file.read_text(encoding="utf-8")
        status = re.search(r"## Status\s*\n+(.+)", text).group(1).strip()  # covered above
        assert status in valid_statuses or superseded_re.match(status), f"{adr_file.name} has invalid status: {status}"

    assert "Superseded by ADR-0010" in (ADR_DIR / "0003-all-vllm-runtime.md").read_text(encoding="utf-8")
    assert "canonical" in (ADR_DIR / "README.md").read_text(encoding="utf-8")


def test_changelog_latest_release_matches_version_and_stays_release_note() -> None:
    version = read_text("VERSION").strip()
    changelog = read_text("CHANGELOG.md")
    headings = re.findall(r"^## \[?([0-9]+\.[0-9]+\.[0-9][^\]\s]*)\]?", changelog, re.MULTILINE)

    assert headings and headings[0] == version
    assert len(changelog.splitlines()) < 90
    assert changelog.count("0.1.0-rc.1") <= 2


def test_retrieval_schemas_use_current_dense_retrieval_contract() -> None:
    for schema_file in ["retrieval_score_request.schema.json", "retrieval_rerank_request.schema.json"]:
        data = json.dumps(load_json(f"specs/schemas/{schema_file}"))
        assert "dense_cosine" in data
        assert "late_interaction_maxsim" not in data
        assert "local-colbert-ko" not in data


def test_model_parameter_docs_match_embedding_projection_policy() -> None:
    text = read_text("docs/operations/model_parameter_discovery.md")
    assert "`local-embed-ko`" in text
    assert "`dimensions`(1024" in text
    assert "`user`" in text
    forbidden = [
        "`local-embed` | `dimensions`, `encoding_format`, `truncate_prompt_tokens`, `user`",
        "`local-embed-ko` | `dimensions`(1024만 허용), `encoding_format`, `truncate_prompt_tokens`, `user`",
    ]
    for phrase in forbidden:
        assert phrase not in text, "user must not be shown as a /v1/models request_parameters projection"
