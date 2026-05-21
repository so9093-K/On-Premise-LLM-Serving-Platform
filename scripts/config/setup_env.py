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
from ai_model_serving.auth_control import AUTH_PROFILE_ENV_KEYS, auth_profile_env_values

IMAGE_CONFIG = ROOT / "configs" / "recommended_images.yaml"
MIN_RISK_VLLM_TRANSFORMERS_VERSION = "4.52.4"


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return lines, values


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
ALWAYS_REFRESH_KEYS = {
    "PROJECT_VERSION",
    "APP_ENV",
    "BUILD_PROFILE",
    "SECRETS_GENERATED_AT",
    *AUTH_PROFILE_ENV_KEYS,
} | (GENERATED_SECRET_KEYS - {"INFISICAL_AUTH_SECRET", "INFISICAL_ENCRYPTION_KEY"})

RETIRED_ENV_KEYS = {
    "RISK_SIREN_BASE_URL",
    "RISK_SIREN_MODEL",
    "RISK_SIREN_TIMEOUT_SECONDS",
    "RISK_SIREN_MAX_CONCURRENCY",
    "RISK_SIREN_QUEUE_TIMEOUT_SECONDS",
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
    minimum_tuple = _version_tuple(MIN_RISK_VLLM_TRANSFORMERS_VERSION)
    for key in ("RISK_VLLM_TRANSFORMERS_VERSION", "RISK_VLLM_TRANSFORMERS_MIN_VERSION"):
        current = values.get(key, "").strip()
        current_tuple = _version_tuple(current) if current else None
        if current_tuple is None or minimum_tuple is None or current_tuple < minimum_tuple:
            if current:
                print(
                    f"migrated {key} from {current} to {MIN_RISK_VLLM_TRANSFORMERS_VERSION}",
                    file=sys.stderr,
                )
            values[key] = MIN_RISK_VLLM_TRANSFORMERS_VERSION


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

def profile_template(profile: str) -> Path:
    if profile == "compose":
        return ROOT / ".env.compose.example"
    if profile == "local":
        return ROOT / ".env.local.example"
    raise ValueError(profile)


def _validated_exposure_mode(exposure_mode: str | None) -> str:
    """Validate the exposure mode against the canonical exposure source."""
    raw = exposure_mode or "private_network"
    exposure_data = load_exposure_data(ROOT)
    return resolve_exposure(raw, exposure_data)


def generated_values(
    profile: str,
    app_env: str | None,
    overrides: dict[str, str],
    auth_mode: str | None = None,
    exposure_mode: str | None = None,
) -> dict[str, str]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    gateway_key = token("ams_gateway")
    admin_key = token("ams_admin")
    internal_token = token("ams_internal")
    grafana_password = token("ams_grafana")
    effective_auth_mode = auth_mode or "local_open"
    effective_exposure_mode = _validated_exposure_mode(exposure_mode)
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
    parser.add_argument("--auth-mode", help="AUTH_MODE를 명시적으로 설정합니다. 기본값은 local_open입니다. (local_open|internal_trusted|private_network|edge_terminated|strict)")
    parser.add_argument("--exposure-mode", help="EXPOSURE_MODE를 명시적으로 설정합니다. 기본값은 private_network입니다. 지원값: private_network|master_open")
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
    if args.sync_runtime_secrets:
        try:
            sync_runtime_secrets_from_env(out_path)
        except Exception as exc:
            print(f"runtime secret 동기화 실패: {out_path}: {exc}", file=sys.stderr)
            return 2
        print(f".runtime/prometheus/admin_api_key 동기화 완료: {out_path}")
        return 0
    if out_path.exists() and not args.force:
        print(f"기존 파일을 덮어쓰지 않습니다: {out_path}. 교체하려면 --force를 사용하세요.", file=sys.stderr)
        print("기존 .env를 유지하면서 Prometheus secret만 복구하려면 `make sync-runtime-secrets`를 사용하세요.", file=sys.stderr)
        return 2
    template = profile_template(args.profile)
    lines, base_values = parse_env_template(template)
    preserved_values = preserve_existing_values(out_path, force=args.force)
    overrides = {
        "PLATFORM_IMAGE": args.platform_image,
        "VLLM_IMAGE": args.vllm_image,
        "RISK_VLLM_IMAGE": args.risk_vllm_image,
        "DCGM_EXPORTER_IMAGE": args.dcgm_exporter_image,
        "PROMETHEUS_IMAGE": args.prometheus_image,
        "GRAFANA_IMAGE": args.grafana_image,
    }
    values = base_values | generated_values(args.profile, args.app_env, overrides, auth_mode=args.auth_mode, exposure_mode=args.exposure_mode) | preserved_values
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
