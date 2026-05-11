#!/usr/bin/env python3
"""Infisical 시크릿 동기화 스크립트.

사용법:
  python scripts/config/infisical_sync.py push            # .env → Infisical (전체)
  python scripts/config/infisical_sync.py push --secrets-only  # 민감 값만 push
  python scripts/config/infisical_sync.py pull            # Infisical → .env
  python scripts/config/infisical_sync.py status          # 현재 상태 비교

make 타겟:
  make secrets-push / secrets-pull / secrets-status
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"

# Infisical 자체 운영에 필요한 키 — .env에 있어도 동기화 제외
INFISICAL_OWN_KEYS: frozenset[str] = frozenset({
    "INFISICAL_AUTH_SECRET",
    "INFISICAL_ENCRYPTION_KEY",
    "INFISICAL_PORT",
    "INFISICAL_URL",
    "INFISICAL_CLIENT_ID",
    "INFISICAL_CLIENT_SECRET",
    "INFISICAL_PROJECT_ID",
    "INFISICAL_ENV",
})

# --secrets-only 플래그 사용 시 이 접미사를 가진 키만 동기화
SENSITIVE_SUFFIXES = (
    "_KEY", "_KEYS", "_TOKEN", "_SECRET", "_PASSWORD", "_CREDENTIAL",
)


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"{path}가 없습니다")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def write_env_updates(path: Path, updates: dict[str, str]) -> None:
    """기존 .env 파일의 특정 키 값만 갱신하고 주석·순서는 보존합니다."""
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                output.append(f"{key}={updates[key]}")
                updated.add(key)
                continue
        output.append(line)
    for key in sorted(set(updates) - updated):
        output.append(f"{key}={updates[key]}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def http_json(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> Any:
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {detail}") from exc


def get_access_token(cfg: dict[str, str]) -> str:
    url = f"{cfg['INFISICAL_URL']}/api/v1/auth/universal-auth/login"
    result = http_json(url, method="POST", body={
        "clientId": cfg["INFISICAL_CLIENT_ID"],
        "clientSecret": cfg["INFISICAL_CLIENT_SECRET"],
    })
    return result["accessToken"]


def fetch_remote(cfg: dict[str, str], token: str) -> dict[str, str]:
    params = urllib.parse.urlencode({
        "workspaceId": cfg["INFISICAL_PROJECT_ID"],
        "environment": cfg["INFISICAL_ENV"],
        "secretPath": "/",
    })
    url = f"{cfg['INFISICAL_URL']}/api/v3/secrets/raw?{params}"
    result = http_json(url, token=token)
    return {s["secretKey"]: s["secretValue"] for s in result.get("secrets", [])}


def _secret_payload(key: str, value: str) -> dict[str, Any]:
    return {"secretKey": key, "secretValue": value, "type": "shared"}


def push_to_infisical(cfg: dict[str, str], token: str, local: dict[str, str]) -> None:
    remote = fetch_remote(cfg, token)
    to_create = {k: v for k, v in local.items() if k not in remote}
    to_update = {k: v for k, v in local.items() if k in remote and remote[k] != v}

    base = cfg["INFISICAL_URL"]
    workspace = cfg["INFISICAL_PROJECT_ID"]
    env = cfg["INFISICAL_ENV"]
    common = {"workspaceId": workspace, "environment": env, "secretPath": "/"}

    if to_create:
        http_json(f"{base}/api/v3/secrets/batch/raw", method="POST", token=token, body={
            **common,
            "secrets": [_secret_payload(k, v) for k, v in to_create.items()],
        })
        sample = ", ".join(sorted(to_create)[:5])
        suffix = "..." if len(to_create) > 5 else ""
        print(f"  생성: {len(to_create)}개 ({sample}{suffix})")

    if to_update:
        http_json(f"{base}/api/v3/secrets/batch/raw", method="PATCH", token=token, body={
            **common,
            "secrets": [_secret_payload(k, v) for k, v in to_update.items()],
        })
        sample = ", ".join(sorted(to_update)[:5])
        suffix = "..." if len(to_update) > 5 else ""
        print(f"  갱신: {len(to_update)}개 ({sample}{suffix})")

    if not to_create and not to_update:
        print("  변경 없음 — Infisical과 이미 동기화 상태")


def cmd_push(cfg: dict[str, str], secrets_only: bool) -> int:
    local = read_env_file(ENV_PATH)
    to_sync = {
        k: v for k, v in local.items()
        if k not in INFISICAL_OWN_KEYS
        and (not secrets_only or any(k.upper().endswith(s) for s in SENSITIVE_SUFFIXES))
    }
    mode = "민감 값만" if secrets_only else "전체"
    print(f"[secrets-push] {len(to_sync)}개 키 → Infisical ({cfg['INFISICAL_ENV']} / {mode})")
    token = get_access_token(cfg)
    push_to_infisical(cfg, token, to_sync)
    print("[secrets-push] 완료")
    return 0


def cmd_pull(cfg: dict[str, str]) -> int:
    print(f"[secrets-pull] Infisical ({cfg['INFISICAL_ENV']}) → .env")
    token = get_access_token(cfg)
    remote = fetch_remote(cfg, token)
    if not remote:
        print("  Infisical에 시크릿이 없습니다. 먼저 make secrets-push를 실행하세요.")
        return 0
    write_env_updates(ENV_PATH, remote)
    print(f"  {len(remote)}개 키 갱신됨")
    print("[secrets-pull] 완료")
    return 0


def cmd_status(cfg: dict[str, str]) -> int:
    local = read_env_file(ENV_PATH)
    token = get_access_token(cfg)
    remote = fetch_remote(cfg, token)

    local_keys = set(local) - INFISICAL_OWN_KEYS
    remote_keys = set(remote)
    both = local_keys & remote_keys
    differs = {k for k in both if local[k] != remote[k]}
    only_local = local_keys - remote_keys
    only_remote = remote_keys - local_keys

    print(f"[secrets-status] 환경: {cfg['INFISICAL_ENV']}")
    print(f"  동기화됨:      {len(both - differs)}개")
    if differs:
        print(f"  값 다름:       {len(differs)}개  {sorted(differs)}")
    if only_local:
        print(f"  .env에만 있음: {len(only_local)}개  {sorted(only_local)}")
    if only_remote:
        print(f"  Infisical에만: {len(only_remote)}개  {sorted(only_remote)}")
    return 0


def load_config() -> dict[str, str]:
    if not ENV_PATH.exists():
        raise FileNotFoundError(
            f"{ENV_PATH}가 없습니다.\n"
            "  make init-env-compose 를 실행하세요."
        )
    cfg = read_env_file(ENV_PATH)
    required = [
        "INFISICAL_URL", "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET",
        "INFISICAL_PROJECT_ID", "INFISICAL_ENV",
    ]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(
            f".env에 값이 없는 키: {', '.join(missing)}\n"
            "make infisical-init 가이드를 따라 Machine Identity를 설정한 후 .env에 채워주세요."
        )
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Infisical 시크릿 동기화")
    parser.add_argument("command", choices=["push", "pull", "status"])
    parser.add_argument(
        "--secrets-only", action="store_true",
        help="push 시 TOKEN, KEY, PASSWORD 등 민감 값만 동기화 (설정 값 제외)",
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except (FileNotFoundError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "push":
            return cmd_push(cfg, args.secrets_only)
        if args.command == "pull":
            return cmd_pull(cfg)
        if args.command == "status":
            return cmd_status(cfg)
    except RuntimeError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
