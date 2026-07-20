from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing dependency: PyYAML. Run `python -m pip install --requirement requirements.lock` "
        "before using make init-env-local/init-env-compose."
    ) from exc

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.lib.cli_kr import KoreanArgumentParser  # noqa: E402
from scripts.compose.resolve_exposure_mode import load_exposure_data, resolve as resolve_exposure  # noqa: E402
from ai_model_serving.auth_control import (
    AUTH_PROFILE_ENV_KEYS,
    auth_profile_env_values,
    auth_profile_exposure_values,
)
from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file

IMAGE_CONFIG = ROOT / "configs" / "recommended_images.yaml"


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def minimum_risk_vllm_transformers_version() -> str:
    return str(
        read_yaml(IMAGE_CONFIG)["images"]["risk_vllm"]["compatibility_pins"][
            "transformers_min"
        ]
    )


def recommended_images() -> dict[str, str]:
    images = read_yaml(IMAGE_CONFIG)["images"]
    return {
        "PLATFORM_IMAGE": str(images["platform"]["default"]),
        "VLLM_IMAGE": str(images["vllm"]["default"]),
        "EMBEDDING_KO_VLLM_IMAGE": str(images.get("embedding_ko_vllm", images["vllm"])["default"]),
        "RISK_VLLM_IMAGE": str(images.get("risk_vllm", images["vllm"])["default"]),
        "DCGM_EXPORTER_IMAGE": str(images["dcgm_exporter"]["default"]),
        "PROMETHEUS_IMAGE": str(images["prometheus"]["default"]),
        "GRAFANA_IMAGE": str(images["grafana"]["default"]),
        "CADVISOR_IMAGE": str(images["cadvisor"]["default"]),
    }


def token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def parse_env_template(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines, load_strict_env_file(path)


def write_env(lines: list[str], values: dict[str, str], out_path: Path) -> None:
    emitted: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0]
            if key in values:
                output.append(f"{key}={values[key]}")
                emitted.add(key)
                continue
        output.append(line)
    for key in sorted(set(values) - emitted):
        output.append(f"{key}={values[key]}")
    out_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


GENERATED_SECRET_KEYS = {
    "API_KEY",
    "API_KEYS",
    "ADMIN_API_KEY",
    "ADMIN_API_KEYS",
    "INTERNAL_SERVICE_TOKEN",
    "INTERNAL_SERVICE_AUTH_REQUIRED",
    "AUTH_MODE",
    "EXPOSURE_MODE",
    "INFISICAL_AUTH_SECRET",
    "INFISICAL_ENCRYPTION_KEY",
}
# GRAFANA_ADMIN_PASSWORD is intentionally excluded from GENERATED_SECRET_KEYS.
# It is a human-facing credential set once on first init and preserved across
# bootstrap re-runs. Service-to-service tokens (above) rotate on each bootstrap;
# a Grafana admin password should not silently change under an operator's session.
#
# INFISICAL_AUTH_SECRET and INFISICAL_ENCRYPTION_KEY are in GENERATED_SECRET_KEYS
# so they are created on first init, but excluded from ALWAYS_REFRESH_KEYS so they
# are NOT rotated on --force re-runs. Changing these after first init destroys all
# stored secrets (Infisical cannot decrypt with a different key).
# EXPOSURE_AUDIENCE must always refresh alongside EXPOSURE_MODE (in
# GENERATED_SECRET_KEYS below): generated_values() validates them as a pair
# (e.g. local_open requires master_open + private_lan), but that validation
# only covers the freshly generated dict. If EXPOSURE_AUDIENCE were preserved
# from an old .env while EXPOSURE_MODE refreshes, main()'s
# `base_values | generated | preserved_values` merge would silently write
# a pair that was never validated together.
ALWAYS_REFRESH_KEYS = {
    "PROJECT_VERSION",
    "APP_ENV",
    "BUILD_PROFILE",
    "SECRETS_GENERATED_AT",
    "EXPOSURE_AUDIENCE",
    *AUTH_PROFILE_ENV_KEYS,
} | (GENERATED_SECRET_KEYS - {"INFISICAL_AUTH_SECRET", "INFISICAL_ENCRYPTION_KEY"})

RETIRED_ENV_KEYS = {
    "RISK_SIREN_BASE_URL",
    "RISK_SIREN_MODEL",
    "RISK_SIREN_TIMEOUT_SECONDS",
    "RISK_SIREN_MAX_CONCURRENCY",
    "RISK_SIREN_QUEUE_TIMEOUT_SECONDS",
    # MAX_REQUEST_BODY_BYTES is a uniform size policy owned by
    # configs/model_serving.yaml (operational_limits.max_request_body_bytes).
    # It was previously duplicated into .env templates where it shadowed the yaml
    # value and silently drifted (deployed .env stuck at 1.25 MB across releases).
    # Retiring it strips the key from existing .env on sync-env so the yaml value
    # becomes authoritative; settings.py still honors an explicit env override.
    "MAX_REQUEST_BODY_BYTES",
}


def preserve_existing_values(out_path: Path, *, force: bool) -> dict[str, str]:
    """Preserve operator edits when regenerating .env.

    `--force` regenerates generated secrets and PROJECT_VERSION, but it should not
    silently erase operator-owned choices such as ports, timeout values, model URLs,
    image tags, Grafana user, or Hugging Face tokens.
    """
    if not force or not out_path.exists():
        return {}
    _, existing = parse_env_template(out_path)
    preserved = {
        key: value
        for key, value in existing.items()
        if value and key not in ALWAYS_REFRESH_KEYS and key not in RETIRED_ENV_KEYS
    }
    if "HF_TOKEN" in preserved and "HUGGING_FACE_HUB_TOKEN" not in preserved:
        preserved["HUGGING_FACE_HUB_TOKEN"] = preserved["HF_TOKEN"]
    return preserved



def normalize_risk_vllm_image(values: dict[str, str], *, explicit_override: bool = False) -> None:
    """Repair stale compose envs created before risk vLLM image split.

    Older .env files used the main VLLM_IMAGE for both generation and risk models.
    Kanana Prompt 2.1B requires a dedicated image whose transformers/vLLM stack
    honors explicit head_dim, so preserving RISK_VLLM_IMAGE=VLLM_IMAGE causes
    bootstrap to fail later. Treat that exact shared-image state as a migration,
    while preserving real operator-owned custom risk images.
    """
    recommended = recommended_images()["RISK_VLLM_IMAGE"]
    risk_image = values.get("RISK_VLLM_IMAGE", "").strip()
    main_image = values.get("VLLM_IMAGE", "").strip()
    if not risk_image or (main_image and risk_image == main_image):
        if explicit_override and risk_image and risk_image == main_image:
            print(
                "warning: --risk-vllm-image equals VLLM_IMAGE; using dedicated Kanana risk image instead",
                file=sys.stderr,
            )
        else:
            print(
                f"migrated RISK_VLLM_IMAGE from shared/base image to dedicated Kanana image: {recommended}",
                file=sys.stderr,
            )
        values["RISK_VLLM_IMAGE"] = recommended


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    parts = value.strip().split(".")
    if len(parts) < 2 or len(parts) > 3:
        return None
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        numbers.append(int(part))
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def normalize_risk_vllm_transformers_pin(values: dict[str, str]) -> None:
    """Repair stale Kanana risk image build pins preserved by older .env files.

    ``--force`` intentionally preserves operator-owned values, but compatibility
    pins are part of the project's runtime contract.  Keeping an old
    RISK_VLLM_TRANSFORMERS_VERSION / RISK_VLLM_TRANSFORMERS_MIN_VERSION can rebuild the dedicated Kanana image with a
    config loader that rejects explicit ``head_dim`` models.
    """
    minimum_version = minimum_risk_vllm_transformers_version()
    minimum_tuple = _version_tuple(minimum_version)
    for key in ("RISK_VLLM_TRANSFORMERS_VERSION", "RISK_VLLM_TRANSFORMERS_MIN_VERSION"):
        current = values.get(key, "").strip()
        current_tuple = _version_tuple(current) if current else None
        if current_tuple is None or minimum_tuple is None or current_tuple < minimum_tuple:
            if current:
                print(
                    f"migrated {key} from {current} to {minimum_version}",
                    file=sys.stderr,
                )
            values[key] = minimum_version


def write_runtime_secrets(values: dict[str, str]) -> None:
    """Write generated runtime secret files consumed by local Compose services.

    Prometheus cannot expand env vars inside prometheus.yml, so it reads the admin
    bearer token from a generated file mounted read-only into the container.
    """
    admin_key = values.get("ADMIN_API_KEY") or values.get("ADMIN_API_KEYS", "").split(",", 1)[0]
    if not admin_key:
        return
    secret_dir = ROOT / ".runtime" / "prometheus"
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / "admin_api_key"
    if secret_path.is_dir():
        try:
            secret_path.rmdir()
        except OSError as exc:
            raise RuntimeError(
                f"{secret_path} must be a file, but it is a non-empty directory. "
                "Remove or move it, then rerun `make sync-runtime-secrets`."
            ) from exc
    secret_path.write_text(admin_key + "\n", encoding="utf-8")
    try:
        # The distroless Prometheus image runs as a non-root UID, so the
        # compose-mounted bearer token must be readable beyond the host owner.
        secret_path.chmod(0o644)
    except OSError:
        pass




def read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(path)
    _, values = parse_env_template(path)
    return values


def sync_runtime_secrets_from_env(env_path: Path) -> None:
    """현재 .env의 admin key를 Prometheus bearer-token 파일로 다시 기록합니다.

    `.env`는 보존하고 `.runtime/prometheus/admin_api_key`만 복구할 때 사용합니다.
    """
    values = read_env_values(env_path)
    admin_key = values.get("ADMIN_API_KEY") or values.get("ADMIN_API_KEYS", "").split(",", 1)[0]
    if not admin_key:
        raise RuntimeError(f"{env_path}에 ADMIN_API_KEY 또는 ADMIN_API_KEYS가 없습니다")
    write_runtime_secrets({"ADMIN_API_KEY": admin_key})


def sync_env_keys(env_path: Path, *, dry_run: bool = False) -> int:
    """템플릿과 기존 .env를 비교해 누락 키 추가, 폐기 키 제거.

    BUILD_PROFILE로 템플릿 자동 감지. 시크릿은 재생성하지 않는다.
    기존 값(HF_TOKEN, API 키 등)은 모두 보존된다.
    """
    if not env_path.exists():
        raise FileNotFoundError(f".env 파일이 없습니다: {env_path}")

    env_lines, existing = parse_env_template(env_path)
    profile = existing.get("BUILD_PROFILE", "compose")
    if profile not in ("local", "compose"):
        profile = "compose"

    template_path = profile_template(profile)
    _, template_values = parse_env_template(template_path)

    added = [k for k in template_values if k not in existing and k not in RETIRED_ENV_KEYS]
    retired_found = [k for k in existing if k in RETIRED_ENV_KEYS]

    if not added and not retired_found:
        print(f"변경 없음: .env가 최신 상태입니다. (profile={profile})")
        return 0

    if added:
        print(f"추가될 키 ({len(added)}개): {', '.join(sorted(added))}")
    if retired_found:
        print(f"제거될 키 ({len(retired_found)}개): {', '.join(sorted(retired_found))}")

    if dry_run:
        print("dry-run: 실제 변경 없음.")
        return 0

    merged = {k: v for k, v in existing.items() if k not in RETIRED_ENV_KEYS}
    for k in added:
        merged[k] = template_values[k]

    if merged.get("HF_TOKEN") and not merged.get("HUGGING_FACE_HUB_TOKEN"):
        merged["HUGGING_FACE_HUB_TOKEN"] = merged["HF_TOKEN"]

    filtered_lines = [
        line for line in env_lines
        if not (
            line.strip()
            and not line.strip().startswith("#")
            and "=" in line.strip()
            and line.strip().split("=", 1)[0] in RETIRED_ENV_KEYS
        )
    ]

    write_env(filtered_lines, merged, env_path)
    print(f"업데이트 완료: {env_path} (profile={profile})")
    return 0

def profile_template(profile: str) -> Path:
    if profile == "compose":
        return ROOT / ".env.compose.example"
    if profile == "local":
        return ROOT / ".env.local.example"
    raise ValueError(profile)


def _validated_exposure_mode(exposure_mode: str) -> str:
    """Validate the exposure mode against the canonical exposure source."""
    exposure_data = load_exposure_data(ROOT)
    return resolve_exposure(exposure_mode, exposure_data)


def generated_values(
    profile: str,
    app_env: str | None,
    overrides: dict[str, str],
    auth_mode: str | None = None,
    exposure_mode: str | None = None,
    exposure_audience: str | None = None,
) -> dict[str, str]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    gateway_key = token("ams_gateway")
    admin_key = token("ams_admin")
    internal_token = token("ams_internal")
    grafana_password = token("ams_grafana")
    effective_auth_mode = auth_mode or "local_open"
    auth_exposure = auth_profile_exposure_values(effective_auth_mode)
    effective_exposure_mode = _validated_exposure_mode(
        exposure_mode or auth_exposure.get("EXPOSURE_MODE", "private_network")
    )
    effective_exposure_audience = (
        exposure_audience
        if exposure_audience is not None
        else (
            ""
            if exposure_mode is not None
            else auth_exposure.get("EXPOSURE_AUDIENCE", "")
        )
    )
    if effective_auth_mode == "local_open" and (
        effective_exposure_mode != "master_open"
        or effective_exposure_audience != "private_lan"
    ):
        raise ValueError(
            "AUTH_MODE=local_open requires "
            "EXPOSURE_MODE=master_open and EXPOSURE_AUDIENCE=private_lan"
        )
    values: dict[str, str] = {
        "PROJECT_VERSION": version,
        "API_KEYS": gateway_key,
        "API_KEY": gateway_key,
        "ADMIN_API_KEY": admin_key,
        "ADMIN_API_KEYS": admin_key,
        "INTERNAL_SERVICE_TOKEN": internal_token,
        "INTERNAL_SERVICE_AUTH_REQUIRED": "true",
        "GRAFANA_ADMIN_PASSWORD": grafana_password,
        "SECRETS_GENERATED_AT": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "EXPOSURE_MODE": effective_exposure_mode,
        "EXPOSURE_AUDIENCE": effective_exposure_audience,
    }
    if profile == "compose":
        values.update({
            "APP_ENV": app_env or "local",
            "BUILD_PROFILE": "compose",
            "GRAFANA_ADMIN_USER": "admin",
            "GRAFANA_ANONYMOUS_ENABLED": "false",
            "INFISICAL_AUTH_SECRET": secrets.token_hex(32),
            "INFISICAL_ENCRYPTION_KEY": secrets.token_hex(16),
        })
        values.update(auth_profile_env_values(effective_auth_mode))
        values.update(recommended_images())
    else:
        values.update({
            "APP_ENV": app_env or "local",
            "BUILD_PROFILE": "local",
            "GRAFANA_ADMIN_USER": "admin",
            "GRAFANA_ANONYMOUS_ENABLED": "false",
        })
        values.update(auth_profile_env_values(effective_auth_mode))
    values.update({k: v for k, v in overrides.items() if v})
    return values


def show_image_tags() -> None:
    images = read_yaml(IMAGE_CONFIG)["images"]
    for name, cfg in images.items():
        print(f"{name}: {cfg['default']}")
        if cfg.get("fallback"):
            print(f"  fallback: {cfg['fallback']}")
        print(f"  purpose: {cfg.get('purpose', '')}")


def build_parser() -> KoreanArgumentParser:
    parser = KoreanArgumentParser(description="한국어 운영자를 위한 .env 생성기입니다. 기존 .env는 기본적으로 덮어쓰지 않습니다.")
    parser.add_argument("--profile", choices=["local", "compose"], default="compose")
    parser.add_argument("--app-env", help="APP_ENV를 덮어씁니다. 기본값은 local profile은 local, compose profile은 local입니다.")
    parser.add_argument("--output", default=".env", help="출력할 env 경로입니다. repository root 기준 상대 경로 또는 절대 경로를 사용할 수 있습니다.")
    parser.add_argument("--force", action="store_true", help="기존 출력 파일을 덮어씁니다.")
    parser.add_argument("--show-image-tags", action="store_true", help="권장 compose image tag를 출력하고 종료합니다.")
    parser.add_argument("--sync-runtime-secrets", action="store_true", help=".env를 다시 쓰지 않고 현재 env 파일에서 .runtime secret file만 동기화합니다.")
    parser.add_argument("--sync-env", action="store_true", help="템플릿과 기존 .env를 비교해 누락 키를 추가하고 폐기 키를 제거합니다. 시크릿은 재생성하지 않습니다.")
    parser.add_argument("--dry-run", action="store_true", help="--sync-env 미리보기. 실제 변경 없음.")
    parser.add_argument("--env-file", help="--sync-env 대상 .env 파일 절대경로. 기본값은 프로젝트 루트 .env.")
    parser.add_argument("--auth-mode", help="AUTH_MODE를 명시적으로 설정합니다. 기본값은 local_open입니다. (local_open|internal_trusted|private_network|edge_terminated|strict)")
    parser.add_argument("--exposure-mode", help="EXPOSURE_MODE를 명시적으로 설정합니다. local_open 기본값은 master_open입니다. 지원값: private_network|master_open")
    parser.add_argument("--exposure-audience", help="EXPOSURE_AUDIENCE를 명시적으로 설정합니다. local_open 기본값은 private_lan입니다.")
    parser.add_argument("--platform-image")
    parser.add_argument("--vllm-image")
    parser.add_argument("--risk-vllm-image")
    parser.add_argument("--dcgm-exporter-image")
    parser.add_argument("--prometheus-image")
    parser.add_argument("--grafana-image")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.show_image_tags:
        show_image_tags()
        return 0
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    if getattr(args, "env_file", None):
        env_file_path = Path(args.env_file)
        if not env_file_path.is_absolute():
            env_file_path = Path.cwd() / env_file_path
        out_path = env_file_path
    if args.sync_runtime_secrets:
        try:
            sync_runtime_secrets_from_env(out_path)
        except Exception as exc:
            print(f"runtime secret 동기화 실패: {out_path}: {exc}", file=sys.stderr)
            return 2
        print(f".runtime/prometheus/admin_api_key 동기화 완료: {out_path}")
        return 0
    if args.sync_env:
        try:
            return sync_env_keys(out_path, dry_run=args.dry_run)
        except Exception as exc:
            print(f"sync-env 실패: {exc}", file=sys.stderr)
            return 2
    if out_path.exists() and not args.force:
        print(f"기존 파일을 덮어쓰지 않습니다: {out_path}. 교체하려면 --force를 사용하세요.", file=sys.stderr)
        print("기존 .env를 유지하면서 Prometheus secret만 복구하려면 `make sync-runtime-secrets`를 사용하세요.", file=sys.stderr)
        return 2
    try:
        template = profile_template(args.profile)
        lines, base_values = parse_env_template(template)
        preserved_values = preserve_existing_values(out_path, force=args.force)
    except RuntimeError as exc:
        print(f"env 파일 오류: {exc}", file=sys.stderr)
        return 2
    overrides = {
        "PLATFORM_IMAGE": args.platform_image,
        "VLLM_IMAGE": args.vllm_image,
        "RISK_VLLM_IMAGE": args.risk_vllm_image,
        "DCGM_EXPORTER_IMAGE": args.dcgm_exporter_image,
        "PROMETHEUS_IMAGE": args.prometheus_image,
        "GRAFANA_IMAGE": args.grafana_image,
    }
    try:
        generated = generated_values(
            args.profile,
            args.app_env,
            overrides,
            auth_mode=args.auth_mode,
            exposure_mode=args.exposure_mode,
            exposure_audience=args.exposure_audience,
        )
    except ValueError as exc:
        print(f"env 정책 오류: {exc}", file=sys.stderr)
        return 2
    values = base_values | generated | preserved_values
    if args.profile == "compose":
        normalize_risk_vllm_image(values, explicit_override=bool(args.risk_vllm_image))
        normalize_risk_vllm_transformers_pin(values)
    if values.get("HF_TOKEN") and not values.get("HUGGING_FACE_HUB_TOKEN"):
        values["HUGGING_FACE_HUB_TOKEN"] = values["HF_TOKEN"]
    write_env(lines, values, out_path)
    if args.profile == "compose" and out_path.resolve() == (ROOT / ".env").resolve():
        write_runtime_secrets(values)
    print(f"wrote {out_path}")
    print(f"profile={args.profile} APP_ENV={values['APP_ENV']} PROJECT_VERSION={values['PROJECT_VERSION']}")
    if args.profile == "compose":
        print("image tags:")
        for key in ["PLATFORM_IMAGE", "VLLM_IMAGE", "EMBEDDING_KO_VLLM_IMAGE", "RISK_VLLM_IMAGE", "DCGM_EXPORTER_IMAGE", "PROMETHEUS_IMAGE", "GRAFANA_IMAGE", "CADVISOR_IMAGE"]:
            print(f"  {key}={values[key]}")
    if args.profile == "compose":
        if out_path.resolve() == (ROOT / ".env").resolve():
            print("generated API_KEYS/API_KEY, ADMIN_API_KEY, INTERNAL_SERVICE_TOKEN, and .runtime/prometheus/admin_api_key; do not commit secrets")
        else:
            print("generated API_KEYS/API_KEY, ADMIN_API_KEY, and INTERNAL_SERVICE_TOKEN; runtime secret file is created only for repository-root .env")
    else:
        print("generated API_KEYS/API_KEY and INTERNAL_SERVICE_TOKEN; do not commit .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
