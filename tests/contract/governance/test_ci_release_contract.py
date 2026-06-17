from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_gitlab_ci_deployment_contract_is_documented_and_operationally_safe() -> None:
    ci = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    doc = (ROOT / "docs/operations/gitlab_cicd_deployment.md").read_text(encoding="utf-8")

    assert "docker:27.5.1-dind" in ci
    assert 'PLATFORM_IMAGE_VERSION="$CI_REGISTRY_IMAGE/platform:release_${APP_VERSION}"' in ci
    assert "release" in ci

    assert 'REGISTRY_USER="${REGISTRY_DEPLOY_USER:-${CI_REGISTRY_USER:-}}"' in deploy
    assert 'REGISTRY_PASSWORD="${REGISTRY_DEPLOY_PASSWORD:-${CI_REGISTRY_PASSWORD:-}}"' in deploy
    assert 'DEPLOY_MODE="${DEPLOY_MODE:-rolling}"' in deploy
    assert "rolling|full" in deploy
    assert "--exclude \".env\"" in deploy
    assert "--exclude \".runtime/\"" in deploy
    assert "--exclude \"model_cache/\"" in deploy
    assert 'COMPOSE_ENV_FILE="${DEPLOY_PATH}/.env"' in deploy
    assert 'compose_run up -d --remove-orphans' in deploy
    assert 'compose_run up -d --no-deps gateway risk-adapter' in deploy
    assert 'get_env_value GATEWAY_BIND_ADDR' in deploy
    assert 'GATEWAY_PROBE_HOST="localhost"' in deploy
    assert 'HEALTH_URL="${GATEWAY_HEALTH_URL:-http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT}/health}"' in deploy
    assert 'RUN_READY_FULL_SMOKE="${RUN_READY_FULL_SMOKE:-0}"' in deploy
    assert 'if [[ "${DEPLOY_MODE}" == "full" && "${RUN_READY_FULL_SMOKE}" == "1" ]]; then' in deploy
    assert 'make ready-full' in deploy
    assert 'make compose-diagnostics || true' in deploy
    assert 'PRUNE_DANGLING_IMAGES="${PRUNE_DANGLING_IMAGES:-1}"' in deploy
    assert 'docker image prune -f --filter dangling=true' in deploy
    preflight_compose = (ROOT / "scripts/compose/preflight_compose.py").read_text(encoding="utf-8")
    assert "COLBERT_KO_MODEL_DIR" not in preflight_compose
    assert "prepare_colbert_ko_vllm_artifact.py" not in preflight_compose
    assert "def _resolve_compose_relative_path" in preflight_compose, (
        "local compose preflight must resolve HF_CACHE_DIR the same way docker compose does"
    )
    assert "_resolve_compose_relative_path(compose_file, cache_raw)" in preflight_compose, (
        "local compose preflight must check the compose-file-relative HF cache path"
    )
    assert "relative HF_CACHE_DIR values are resolved from compose file directory" in preflight_compose, (
        "local compose preflight must log the HF cache path base"
    )

    assert "Docker executor" in doc
    assert "Shell executor" in doc
    assert "REGISTRY_DEPLOY_USER" in doc
    assert "DEPLOY_MODE=rolling" in doc
    assert "DEPLOY_MODE=full" in doc
    assert "GATEWAY_BIND_ADDR=<175 internal IP>" in doc
    assert "RUN_READY_FULL_SMOKE" in doc
    assert "PRUNE_DANGLING_IMAGES" in doc


def test_gitlab_ci_vllm_derived_build_contract() -> None:
    """Derived vLLM image CI stays explicit about pipeline intent and release failure."""
    ci = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    ci_parsed = yaml.safe_load(ci)

    build_script_path = ROOT / "scripts/ci/build_vllm_derived_images.sh"
    assert build_script_path.exists(), (
        "scripts/ci/build_vllm_derived_images.sh must exist — "
        "build-vllm-derived delegates its shell logic here"
    )
    build_script = build_script_path.read_text(encoding="utf-8")

    assert "build-vllm-derived:" in ci
    assert "VLLM_BASE_IMAGE" in ci, (
        ".gitlab-ci.yml must define VLLM_BASE_IMAGE as the common base for all vLLM-derived images"
    )

    build_job = ci_parsed.get("build-vllm-derived", {})

    assert build_job.get("when") != "manual", (
        "build-vllm-derived must not be a manual job — it runs automatically when "
        "BUILD_VLLM_DERIVED=1 or DEPLOY_MODE=full is set on a release/tag pipeline"
    )
    assert build_job.get("allow_failure") is False, (
        "build-vllm-derived must have allow_failure: false explicitly — "
        "if it runs and fails, it is a release failure"
    )

    # only: refs + variables — job runs on matching ref AND matching variable
    only = build_job.get("only", {})
    assert isinstance(only, dict), (
        "build-vllm-derived only: must use refs+variables form to gate on pipeline intent variable"
    )
    only_vars = only.get("variables", [])
    assert any("BUILD_VLLM_DERIVED" in v for v in only_vars), (
        "build-vllm-derived only.variables must include BUILD_VLLM_DERIVED == \"1\" "
        "so build-only pipelines can trigger the derived image build"
    )
    assert any("DEPLOY_MODE" in v for v in only_vars), (
        "build-vllm-derived only.variables must include DEPLOY_MODE == \"full\" "
        "as an alternative trigger for full runtime deploy pipelines"
    )

    build_job_script = " ".join(str(s) for s in build_job.get("script", []))
    assert "build_vllm_derived_images.sh" in build_job_script, (
        "build-vllm-derived must delegate to scripts/ci/build_vllm_derived_images.sh"
    )

    assert "set -euo pipefail" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must use 'set -euo pipefail'"
    )
    assert "Dockerfile.risk-vllm-kanana" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must reference ops/docker/Dockerfile.risk-vllm-kanana"
    )
    assert "docker pull \"${RESOLVED_VLLM_BASE_IMAGE}\"" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must pull the resolved shared vLLM base image once "
        "before building the risk derived image"
    )
    for image_var in [
        "RISK_VLLM_IMAGE_SHA",
        "RISK_VLLM_IMAGE_REF",
    ]:
        assert f'docker push "${{{image_var}}}"' in build_script, (
            f"scripts/ci/build_vllm_derived_images.sh must push {image_var}"
        )

    assert "risk-vllm-kanana:${CI_COMMIT_TAG}" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must push risk-vllm-kanana:<tag> on CI_COMMIT_TAG pipelines"
    )
    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    assert 'pull_preflight_image "risk-vllm-kanana" "${RISK_VLLM_IMAGE_TO_DEPLOY}"' in deploy
    assert "set_env_value RISK_VLLM_IMAGE" in deploy
