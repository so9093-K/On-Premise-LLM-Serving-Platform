from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_gitlab_ci_deployment_contract_is_documented_and_operationally_safe() -> None:
    ci = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    doc = (ROOT / "docs/operations/gitlab_cicd_deployment.md").read_text(encoding="utf-8")

    assert "docker:27.5.1-dind" in ci
    assert 'PLATFORM_IMAGE_VERSION="$CI_REGISTRY_IMAGE/platform:release_${APP_VERSION}"' in ci
    assert "release" in ci
    assert "dotenv:" not in ci, (
        "GitLab 12.1.1 does not support artifacts:reports:dotenv"
    )
    for unsupported_key in ("workflow:", "rules:", "needs:"):
        assert unsupported_key not in ci, (
            f"GitLab 12.1.1 compatibility forbids {unsupported_key}"
        )
    assert "dependencies:" in ci
    assert "- build-platform" in ci
    assert ". build/platform-image.env" in ci
    assert 'PLATFORM_IMAGE_TO_DEPLOY="${PLATFORM_IMAGE_DIGEST}"' in ci
    assert "from ai_model_serving.model_cache import prepare_model_snapshot" in ci
    assert "load_main_model_catalog(Path('/app/configs/main_model_profiles.yaml'))" in ci

    assert 'REGISTRY_USER="${REGISTRY_DEPLOY_USER:-${CI_REGISTRY_USER:-}}"' in deploy
    assert 'REGISTRY_PASSWORD="${REGISTRY_DEPLOY_PASSWORD:-${CI_REGISTRY_PASSWORD:-}}"' in deploy
    assert 'DEPLOY_MODE="rolling"' in deploy
    assert "rolling|full" in deploy
    assert "--exclude \".env\"" in deploy
    assert "--exclude \".runtime/\"" in deploy
    assert "--exclude \"model_cache/\"" in deploy
    assert '--exclude "scripts/build/"' not in deploy
    assert 'COMPOSE_ENV_FILE="${DEPLOY_PATH}/.env"' in deploy
    # Per-service recreation must use --no-deps: without it, `up -d gateway`
    # cascades into recreating gateway's whole depends_on graph (the vLLM fleet),
    # because the shared .env changes every service's config-hash each deploy.
    assert 'compose_run up -d --no-deps --remove-orphans' in deploy
    assert 'compose_run up -d --remove-orphans "${CHANGED_SERVICES' not in deploy, (
        "per-service converge must pass --no-deps so it does not cascade into the dependency graph"
    )
    # Full deploy must converge per-service (recreate only changed services),
    # not cold-restart the whole fleet on every release. Scoping is by resolved
    # image ID, because the shared .env (loaded by every service via env_file)
    # changes every deploy and so Compose's config-hash rehashes the whole fleet.
    assert "list_services_needing_recreate" in deploy, (
        "full deploy must converge per-service, not cold-restart the whole fleet"
    )
    assert "docker image inspect -f '{{ .Id }}'" in deploy, (
        "per-service convergence must compare the candidate resolved image ID"
    )
    assert "docker inspect -f '{{ .Image }}'" in deploy, (
        "per-service convergence must read the running container's image ID"
    )
    assert 'compose_run up -d --no-deps --remove-orphans "${CHANGED_SERVICES[@]}"' in deploy, (
        "full deploy must recreate only the changed service set, without cascading to deps"
    )
    # Forward deploy and rollback must compute the recreate set the same way, so a
    # failed deploy reverts exactly what it changed (no inconsistent split state).
    assert "compute_recreate_set" in deploy, (
        "forward deploy and rollback must share one recreate-set computation"
    )
    assert 'compute_recreate_set "${PREVIOUS_RELEASE}"' in deploy, (
        "forward deploy computes the recreate set against the previous release"
    )
    assert 'compute_recreate_set "${RELEASE_PATH}"' in deploy, (
        "rollback computes the recreate set symmetrically against the failed candidate"
    )
    # Config-content changes do not change the image ID; map them to the services
    # that actually consume each file. Only main-llm-vllm mounts configs/ and the
    # chat template, so a model-profile/template change must not restart other models.
    assert "configs/main_model_profiles.yaml configs/gemma4_chat_template.jinja" in deploy, (
        "model-profile/chat-template changes must map to main-llm-vllm only"
    )
    assert 'compose_run up -d --no-deps gateway risk-adapter' in deploy
    assert 'compose_run pull gateway admin-sidecar risk-adapter' in deploy
    assert 'compose_run pull gateway admin-sidecar risk-adapter prometheus grafana' not in deploy
    assert 'COMPOSE_SERVICE_ENV_FILE="${COMPOSE_ENV_FILE}"' in deploy
    assert '--project-name "${COMPOSE_PROJECT_NAME:-compose}"' in deploy
    assert 'get_env_value GATEWAY_BIND_ADDR' in deploy
    assert 'GATEWAY_PROBE_HOST="localhost"' in deploy
    assert 'HEALTH_URL="${GATEWAY_HEALTH_URL:-http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT}/health}"' in deploy
    assert 'RUN_READY_FULL_SMOKE="${RUN_READY_FULL_SMOKE:-1}"' in deploy
    assert 'if [[ "${DEPLOY_MODE}" == "full" ]]; then' in deploy
    assert 'make ready-full' in deploy
    assert 'make compose-diagnostics || true' in deploy
    assert "restore_previous_release()" in deploy
    assert "render_main_model_boot_override.py" in deploy
    assert "main-model cache prepare failed" in deploy
    assert 'compose_args+=(-f "${MAIN_MODEL_BOOT_OVERRIDE}")' in deploy
    assert '"${RESTORING_RELEASE:-0}" != "1"' in deploy
    assert 'MAIN_MODEL_BOOT_OVERRIDE="$(' in deploy
    assert 'mktemp "${TMPDIR:-/tmp}/main-model-boot.XXXXXX.yaml"' in deploy
    assert "trap cleanup_generated_files EXIT" in deploy
    assert "exposure mode resolver is missing" in deploy
    assert "exposure overlay not found" in deploy
    assert "deploying without overlay" not in deploy
    assert "COMPOSE_PROJECT_EFFECTIVE" in deploy
    assert ".env.snapshot" not in deploy
    assert "capture_main_model_runtime_override.py" not in deploy
    assert deploy.index("validating compose config with") < deploy.index(
        "preparing main-model cache"
    )
    assert 'compose_run up -d --no-deps admin-sidecar' in deploy
    assert "previous release, .env, services, and release links restored" in deploy
    assert "legacy live tree detected; snapshotting it" in deploy
    assert 'LEGACY_RELEASE_CREATED="${DEPLOY_PATH}/releases/legacy-' in deploy
    assert "restore_release_links()" in deploy
    assert 'unset "${COMPOSE_EXPORTED_KEYS[@]}"' in deploy
    assert 'done < "${COMPOSE_ENV_FILE}"' in deploy
    assert deploy.index("configure_release_context()") < deploy.index(
        "trap unexpected_failure_after_env_backup ERR"
    )
    assert deploy.index("LINKS_MUTATED=1") < deploy.index(
        'mv -Tf "${CURRENT_LINK_TMP}" "${DEPLOY_PATH}/current"'
    )
    assert deploy.index('mv -Tf "${RUNTIME_LINK_TMP}"') < deploy.rindex("trap - ERR")
    assert 'PRUNE_DANGLING_IMAGES="${PRUNE_DANGLING_IMAGES:-1}"' in deploy
    assert 'docker image prune -f --filter dangling=true' in deploy
    preflight_compose = (ROOT / "scripts/compose/preflight_compose.py").read_text(encoding="utf-8")
    assert "COLBERT_KO_MODEL_DIR" not in preflight_compose
    assert "prepare_colbert_ko_vllm_artifact.py" not in preflight_compose
    assert "from ai_model_serving.main_model_boot import" in preflight_compose
    assert "resolve_compose_relative_path(cache_raw, Path(compose_file))" in preflight_compose
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


def test_release_link_restore_recovers_both_generation_pointers(tmp_path) -> None:
    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    function_body = deploy.split("restore_release_links() {", 1)[1].split(
        "\n}\n\nrestore_previous_release()", 1
    )[0]
    deployment = tmp_path / "deployment"
    releases = deployment / "releases"
    old_release = releases / "old"
    new_release = releases / "new"
    old_release.mkdir(parents=True)
    new_release.mkdir()
    (deployment / "current").symlink_to("releases/new")
    (deployment / "runtime-current").symlink_to("releases/new")

    shell = f"""
set -euo pipefail
DEPLOY_PATH={deployment}
PREVIOUS_CURRENT_LINK=releases/old
PREVIOUS_RUNTIME_LINK=releases/old
LINKS_MUTATED=1
restore_release_links() {{
{function_body}
}}
restore_release_links
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert os.readlink(deployment / "current") == "releases/old"
    assert os.readlink(deployment / "runtime-current") == "releases/old"


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
    assert "ops/images/vllm-gemma4-audio/Dockerfile" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must build the 12B multimodal audio runtime image"
    )
    assert "docker pull \"${RESOLVED_VLLM_BASE_IMAGE}\"" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must pull the resolved shared vLLM base image once "
        "before building derived runtime images"
    )
    for image_var in [
        "RISK_VLLM_IMAGE_SHA",
        "RISK_VLLM_IMAGE_REF",
        "AUDIO_VLLM_IMAGE_SHA",
        "AUDIO_VLLM_IMAGE_REF",
    ]:
        assert f'docker push "${{{image_var}}}"' in build_script, (
            f"scripts/ci/build_vllm_derived_images.sh must push {image_var}"
        )

    assert "risk-vllm-kanana:${CI_COMMIT_TAG}" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must push risk-vllm-kanana:<tag> on CI_COMMIT_TAG pipelines"
    )
    assert "vllm-gemma4-audio:${CI_COMMIT_TAG}" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must push vllm-gemma4-audio:<tag> on CI_COMMIT_TAG pipelines"
    )
    assert "AUDIO_VLLM_IMAGE_DIGEST" in build_script and "build/audio-image.env" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must emit the audio image digest artifact"
    )
    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    assert 'pull_preflight_image "risk-vllm-kanana" "${RISK_VLLM_IMAGE_TO_DEPLOY}"' in deploy
    assert "set_env_value RISK_VLLM_IMAGE" in deploy
