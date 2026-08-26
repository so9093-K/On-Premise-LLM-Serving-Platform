"""scripts/auth의 auth_plan/auth_apply/exposure_plan/exposure_apply CLI를 검증한다:
모드 전환 시 secret을 마스킹해서 보여주는지, .env에 실제로 올바른 값을 쓰는지,
잘못된 env 파일을 traceback 없이 명확한 에러로 보고하는지."""

from __future__ import annotations

import pytest

from scripts.auth import auth_apply, auth_plan, exposure_apply, exposure_plan


def test_auth_plan_masks_secrets_and_reports_changes():
    current = {
        "APP_ENV": "staging",
        "AUTH_MODE": "local_open",
        "API_KEY_REQUIRED": "false",
        "API_KEYS": "secret-value",
    }
    plan = auth_plan.build_plan(current, "strict")
    rendered = auth_plan.render_plan(plan)
    assert "secret-value" not in rendered
    assert "AUTH_MODE" in rendered
    assert "strict" in rendered
    assert any(change["key"] == "FASTAPI_DOCS_ENABLED" and change["after"] == "false" for change in plan["env_changes"])


def test_auth_apply_without_yes_is_successful_dry_run(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("AUTH_MODE=local_open\nAPI_KEY_REQUIRED=false\n", encoding="utf-8")
    rc = auth_apply.main(["--mode", "strict", "--env", str(env_path)])
    assert rc == 0
    assert "AUTH_MODE=local_open" in env_path.read_text(encoding="utf-8")


def test_auth_apply_updates_only_profile_flags(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("AUTH_MODE=local_open\nAPI_KEYS=keep-me\nAPI_KEY_REQUIRED=false\n", encoding="utf-8")
    rc = auth_apply.main(["--mode", "strict", "--env", str(env_path), "--yes"])
    assert rc == 0
    text = env_path.read_text(encoding="utf-8")
    assert "AUTH_MODE=strict" in text
    assert "API_KEY_REQUIRED=true" in text
    assert "ADMIN_API_KEY_REQUIRED=true" in text
    assert "INTERNAL_SERVICE_AUTH_REQUIRED=true" in text
    assert "FASTAPI_DOCS_ENABLED=false" in text
    assert "API_KEYS=keep-me" in text


def test_auth_apply_local_open_applies_trusted_lan_full_stack_policy(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "AUTH_MODE=strict\n"
        "APP_ENV=production\n"
        "EXPOSURE_MODE=private_network\n"
        "EXPOSURE_AUDIENCE=\n",
        encoding="utf-8",
    )

    rc = auth_apply.main(
        ["--mode", "local_open", "--env", str(env_path), "--yes"]
    )

    assert rc == 0
    text = env_path.read_text(encoding="utf-8")
    assert "AUTH_MODE=local_open" in text
    assert "APP_ENV=local" in text
    assert "EXPOSURE_MODE=master_open" in text
    assert "EXPOSURE_AUDIENCE=private_lan" in text


def test_auth_plan_local_open_declares_exposure_changes():
    plan = auth_plan.build_plan(
        {
            "AUTH_MODE": "strict",
            "EXPOSURE_MODE": "private_network",
            "EXPOSURE_AUDIENCE": "",
        },
        "local_open",
    )
    changes = {change["key"]: change["after"] for change in plan["env_changes"]}
    assert changes["EXPOSURE_MODE"] == "master_open"
    assert changes["EXPOSURE_AUDIENCE"] == "private_lan"


# plan/apply 네 CLI 모두 같은 계약이다: 깨진 .env는 traceback이 아니라 exit 2와
# 한국어 한 줄 진단으로 보고해야 한다. 예전엔 auth 쌍과 exposure 쌍이 같은 본문을
# 각각 한 벌씩 들고 있었다.
@pytest.mark.parametrize(
    ("module", "mode", "duplicated_key"),
    [
        (auth_plan, "strict", "AUTH_MODE"),
        (auth_apply, "strict", "AUTH_MODE"),
        (exposure_plan, "private_network", "EXPOSURE_MODE"),
        (exposure_apply, "private_network", "EXPOSURE_MODE"),
    ],
    ids=["auth_plan", "auth_apply", "exposure_plan", "exposure_apply"],
)
def test_plan_apply_report_invalid_env_without_traceback(
    module, mode, duplicated_key, tmp_path, capsys
):
    env_path = tmp_path / ".env"
    values = {"AUTH_MODE": ("local_open", "strict"), "EXPOSURE_MODE": ("private_network", "master_open")}
    first, second = values[duplicated_key]
    env_path.write_text(
        f"{duplicated_key}={first}\n{duplicated_key}={second}\n", encoding="utf-8"
    )

    assert module.main(["--mode", mode, "--env", str(env_path)]) == 2

    captured = capsys.readouterr()
    assert "env 파일 오류:" in captured.err
    assert f"duplicate env key '{duplicated_key}'" in captured.err
    assert "Traceback" not in captured.err
