from __future__ import annotations

from scripts.validation.governance.terminology import terminology_violations


def test_user_facing_noncanonical_term_is_reported(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "01_overview.md").write_text("Admin Sidecar controls runtimes.\n", encoding="utf-8")

    violations = terminology_violations(tmp_path)

    assert violations == [
        "docs/01_overview.md:1: noncanonical display term 'Admin Sidecar'; use 'Runtime Controller'"
    ]


def test_compound_noncanonical_runtime_controller_term_is_reported(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "03_system_components.md").write_text(
        "Admin / Control Sidecar controls runtimes.\n",
        encoding="utf-8",
    )

    assert terminology_violations(tmp_path) == [
        "docs/03_system_components.md:1: noncanonical display term "
        "'Admin / Control Sidecar'; use 'Runtime Controller'"
    ]


def test_lowercase_noncanonical_runtime_controller_term_is_reported(tmp_path):
    src = tmp_path / "src" / "ai_model_serving"
    src.mkdir(parents=True)
    (src / "api_examples.py").write_text(
        'DETAIL = "admin sidecar is not configured"\n',
        encoding="utf-8",
    )

    assert terminology_violations(tmp_path) == [
        "src/ai_model_serving/api_examples.py:1: noncanonical display term "
        "'admin sidecar'; use 'Runtime Controller'"
    ]


def test_runtime_cli_noncanonical_service_term_is_reported(tmp_path):
    cli = tmp_path / "scripts" / "validation" / "runtime"
    cli.mkdir(parents=True)
    (cli / "cli.py").write_text(
        'HELP = "Risk Adapter base URL"\n',
        encoding="utf-8",
    )

    assert terminology_violations(tmp_path) == [
        "scripts/validation/runtime/cli.py:1: noncanonical display term "
        "'Risk Adapter'; use 'Risk Signal Service'"
    ]


def test_adr_history_is_outside_display_terminology_gate(tmp_path):
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-history.md").write_text("Risk Adapter was the old name.\n", encoding="utf-8")

    assert terminology_violations(tmp_path) == []


