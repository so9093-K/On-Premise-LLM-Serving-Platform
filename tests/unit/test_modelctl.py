from __future__ import annotations

import json

from scripts.models import modelctl


def test_modelctl_status_document_includes_lifecycle_and_gpu_budget():
    registry = modelctl.load_registry(modelctl.ROOT)
    doc = modelctl.status_document(modelctl.ROOT, registry)
    assert doc["model_count"] == 4
    assert doc["public_model_count"] == 3
    assert doc["lifecycle_states"] == {"active": 3, "retired": 1}
    assert doc["gpu"]["total_gpu_memory_utilization"] == 0.765
    assert doc["alignment_issues"] == []


def test_modelctl_validate_passes_current_registry(capsys):
    rc = modelctl.main(["validate"])
    assert rc == 0
    assert "PASS" in capsys.readouterr().out


def test_modelctl_list_json_contains_runtime_services(capsys):
    rc = modelctl.main(["list", "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    rows = {row["id"]: row for row in data["models"]}
    assert rows["local-main"]["runtime_service"] == "main_llm"
    assert rows["risk-siren"]["state"] == "retired"



def test_modelctl_propose_add_is_plan_only_and_reports_affected_files(capsys):
    rc = modelctl.main([
        "propose-add",
        "--id", "new-main",
        "--role", "main_llm",
        "--upstream-model-id", "example/new-main",
        "--port", "9499",
        "--endpoint", "/v1/new-main",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "파일 쓰기: 없음" in out
    assert "configs/model_catalog.yaml" in out
    assert "make operator-reports" in out


def test_modelctl_propose_remove_existing_model_is_plan_only(capsys):
    rc = modelctl.main(["propose-remove", "local-main"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "propose-remove" in out
    assert "deprecation_deadline" in out
    assert "파일 쓰기: 없음" in out


def test_modelctl_propose_add_blocks_existing_identity(capsys):
    rc = modelctl.main([
        "propose-add",
        "--id", "local-main",
        "--role", "main_llm",
        "--upstream-model-id", "example/local-main",
        "--port", "9401",
        "--endpoint", "/v1/chat/completions",
    ])
    assert rc == 1
    assert "차단 조건" in capsys.readouterr().out


def test_modelctl_write_plan_supports_absolute_output_dir(tmp_path, capsys):
    rc = modelctl.main([
        "propose-add",
        "--id", "new-main",
        "--role", "main_llm",
        "--upstream-model-id", "example/new-main",
        "--port", "9499",
        "--endpoint", "/v1/new-main",
        "--write-plan",
        "--write-patch",
        "--output-dir", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "생성된 artifact" in out
    assert (tmp_path / "propose-add-new-main.plan.json").exists()
    assert (tmp_path / "propose-add-new-main.plan.md").exists()
    assert (tmp_path / "propose-add-new-main.patch-scaffold.md").exists()
