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
    auth_profile_exposure_mismatch,
)
from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file

IMAGE_CONFIG = ROOT / "configs" / "recommended_images.yaml"


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
        "LOKI_IMAGE": str(images["loki"]["default"]),
        "ALLOY_IMAGE": str(images["alloy"]["default"]),
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
}
# GRAFANA_ADMIN_PASSWORD는 GENERATED_SECRET_KEYS에서 의도적으로 제외됩니다.
# 최초 init 시 한 번 설정되고 이후 bootstrap을 다시 돌려도 보존되는, 사람이 쓰는
# 자격 증명이기 때문입니다. 서비스 간 토큰(위 목록)은 bootstrap마다 재발급되지만,
# Grafana admin 비밀번호는 운영자의 세션 도중 조용히 바뀌면 안 됩니다.
#
# EXPOSURE_AUDIENCE는 항상 EXPOSURE_MODE와 함께 갱신되어야 합니다(아래
# GENERATED_SECRET_KEYS에서): generated_values()가 둘을 쌍으로 검증하지만
# (예: local_open은 master_open + private_lan을 요구), 이 검증은 새로 생성된
# dict에만 적용됩니다. EXPOSURE_MODE는 갱신되는데 EXPOSURE_AUDIENCE가 기존 .env
# 값으로 보존된다면, main()의 `base_values | generated | preserved_values` 병합이
# 한 번도 함께 검증된 적 없는 쌍을 조용히 기록하게 됩니다.
ALWAYS_REFRESH_KEYS = {
    "APP_ENV",
    "BUILD_PROFILE",
    "SECRETS_GENERATED_AT",
    "EXPOSURE_AUDIENCE",
    *AUTH_PROFILE_ENV_KEYS,
} | GENERATED_SECRET_KEYS

def _removed_env_keys() -> frozenset[str]:
    """sync-env가 기존 .env에서 제거하는 키. 단일 소스는 env_contract.yaml이다.

    여기에 목록을 복제하지 않는 이유는 그 순간 두 소스가 갈라지기 때문이다 --
    validate는 통과하는데 sync-env는 다르게 동작하는, 가장 알아채기 어려운 형태의
    드리프트가 된다. validate_env_contract.py가 같은 항목으로 "예시 파일에 다시
    등장하지 않았는지"를 검증한다.
    """
    contract = yaml.safe_load((ROOT / "configs" / "env_contract.yaml").read_text(encoding="utf-8"))
    return frozenset(contract.get("removed_keys") or {})


REMOVED_ENV_KEYS = _removed_env_keys()


def preserve_existing_values(out_path: Path, *, force: bool) -> dict[str, str]:
    """Preserve operator edits when regenerating .env.

    `--force` regenerates generated secrets, but it should not
    silently erase operator-owned choices such as ports, timeout values, model URLs,
    image tags, Grafana user, or Hugging Face tokens.
    """
    if not force or not out_path.exists():
        return {}
    _, existing = parse_env_template(out_path)
    preserved = {
        key: value
        for key, value in existing.items()
        if value and key not in ALWAYS_REFRESH_KEYS and key not in REMOVED_ENV_KEYS
    }
    if "HF_TOKEN" in preserved and "HUGGING_FACE_HUB_TOKEN" not in preserved:
        preserved["HUGGING_FACE_HUB_TOKEN"] = preserved["HF_TOKEN"]
    return preserved



def ensure_risk_vllm_image(values: dict[str, str]) -> None:
    """Fill in RISK_VLLM_IMAGE only when it is unset.

    2026-07-24부터 VLLM_IMAGE/RISK_VLLM_IMAGE는 같은 vLLM unified 이미지를
    가리키는 게 정상이다(Gemma4 멀티모달 패치 + Kanana head_dim 패치가 한
    이미지에 같이 들어있고, 각 patch는 서로 무관한 모델에는 no-op이다). 예전엔
    둘이 같으면 "shared/base image로의 실수"로 보고 강제로 되돌리는 마이그레이션
    가드가 있었는데, 지금은 정확히 그 상태가 의도된 정상 상태라 제거했다.
    """
    recommended = recommended_images()["RISK_VLLM_IMAGE"]
    risk_image = values.get("RISK_VLLM_IMAGE", "").strip()
    if not risk_image:
        values["RISK_VLLM_IMAGE"] = recommended


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
        # distroless Prometheus 이미지는 non-root UID로 실행되므로
        # compose에 마운트된 bearer token은 호스트 소유자 이외에도 읽을 수 있어야 합니다.
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
    """템플릿의 누락 키를 추가하고, 명시적으로 등록된 폐기 키만 제거한다.

    BUILD_PROFILE로 템플릿 자동 감지. 시크릿은 재생성하지 않는다.
    기존 값(HF_TOKEN, API 키 등)은 모두 보존된다.

    제거 대상은 env_contract.yaml의 `removed_keys`에 등록된 것뿐이다.
    "템플릿에 없는 키"를 제거 기준으로 삼지 않는 것이 ADR-0013의 핵심이다 --
    배포 서버의 .env에는 템플릿에 존재한 적 없는 서버 전용 설정
    (예: MAIN_MODEL_STATE_PATH)이 정상적으로 들어있고, deploy 스크립트가
    이미지 참조 갱신 직후 이 함수를 호출하기 때문에 그 기준으로 지우면
    배포할 때마다 운영 설정이 사라진다.
    """
    if not env_path.exists():
        raise FileNotFoundError(f".env 파일이 없습니다: {env_path}")

    env_lines, existing = parse_env_template(env_path)
    profile = existing.get("BUILD_PROFILE", "compose")
    if profile not in ("local", "compose"):
        profile = "compose"

    template_path = profile_template(profile)
    _, template_values = parse_env_template(template_path)

    added = [k for k in template_values if k not in existing and k not in REMOVED_ENV_KEYS]
    removed = [k for k in existing if k in REMOVED_ENV_KEYS]

    if not added and not removed:
        print(f"변경 없음: .env가 최신 상태입니다. (profile={profile})")
        return 0

    if added:
        print(f"추가될 키 ({len(added)}개): {', '.join(sorted(added))}")
    if removed:
        print(f"제거될 키 ({len(removed)}개): {', '.join(sorted(removed))}")

    if dry_run:
        print("dry-run: 실제 변경 없음.")
        return 0

    merged = {k: v for k, v in existing.items() if k not in REMOVED_ENV_KEYS}
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
            and line.strip().split("=", 1)[0] in REMOVED_ENV_KEYS
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
    exposure_mismatch = auth_profile_exposure_mismatch(
        effective_auth_mode, effective_exposure_mode, effective_exposure_audience
    )
    if exposure_mismatch is not None:
        raise ValueError(exposure_mismatch)
    # PROJECT_VERSION은 쓰지 않는다 -- VERSION 파일이 소유하고 settings.py가 env를
    # 우선하므로, .env에 복제하면 그 값이 파일을 가린 채 낡는다(env_contract.yaml
    # removed_keys 참고).
    values: dict[str, str] = {
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


def build_parser() -> KoreanArgumentParser:
    parser = KoreanArgumentParser(description="한국어 운영자를 위한 .env 생성기입니다. 기존 .env는 기본적으로 덮어쓰지 않습니다.")
    parser.add_argument("--profile", choices=["local", "compose"], default="compose")
    parser.add_argument("--app-env", help="APP_ENV를 덮어씁니다. 기본값은 local profile은 local, compose profile은 local입니다.")
    parser.add_argument("--output", default=".env", help="출력할 env 경로입니다. repository root 기준 상대 경로 또는 절대 경로를 사용할 수 있습니다.")
    parser.add_argument("--force", action="store_true", help="기존 출력 파일을 덮어씁니다.")
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
        ensure_risk_vllm_image(values)
    if values.get("HF_TOKEN") and not values.get("HUGGING_FACE_HUB_TOKEN"):
        values["HUGGING_FACE_HUB_TOKEN"] = values["HF_TOKEN"]
    write_env(lines, values, out_path)
    if args.profile == "compose" and out_path.resolve() == (ROOT / ".env").resolve():
        write_runtime_secrets(values)
    print(f"wrote {out_path}")
    print(f"profile={args.profile} APP_ENV={values['APP_ENV']}")
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
