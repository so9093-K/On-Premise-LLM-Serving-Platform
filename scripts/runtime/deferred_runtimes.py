"""배포 시점에 GPU VRAM 예산이 빠듯할 때 embedding/embedding-ko/risk-prompt 같은
secondary 런타임을 처음부터 정지 상태로 둘지 정하고, 그 결정을 Gateway의
runtime-state.json에 기록한다. defer를 빠뜨리면 main model이 부팅 중 GPU 메모리
부족으로 기동을 실패할 수 있고, 반대로 잘못 defer하면 배포 직후부터 해당
엔드포인트가 이유 없이 503을 낸다. scripts/ci/deploy_gitlab_compose.sh가 실제
배포 시퀀스에서 이 스크립트를 하드 게이트로 호출하므로, 여기서 실패하면
배포 자체가 중단되고 env가 롤백된다."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.runtime_topology import RuntimeTopology, load_runtime_topology


def _items(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_deploy_profile_runtimes(config_root: Path, profile: str) -> list[str]:
    # --runtimes를 배포마다 손으로 나열하는 대신, configs/deploy_profiles.yaml에
    # 미리 정의해둔 조합(예: GPU가 작은 호스트용 프로필)을 이름으로 재사용하기 위함.
    if not profile:
        return []
    path = config_root / "configs/deploy_profiles.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise SystemExit("configs/deploy_profiles.yaml must define profiles")
    item = profiles.get(profile)
    if not isinstance(item, dict):
        valid = ", ".join(sorted(str(key) for key in profiles))
        raise SystemExit(f"unknown deploy runtime profile: {profile}; valid values: {valid}")
    runtimes = item.get("deferred_runtimes", [])
    if not isinstance(runtimes, list) or not all(isinstance(value, str) for value in runtimes):
        raise SystemExit(f"deploy profile {profile} must define deferred_runtimes as a string list")
    return runtimes


def resolve_deferred_runtimes(
    topology: RuntimeTopology,
    raw: str,
) -> tuple[list[str], list[str]]:
    service_by_key = topology.service_by_key
    key_by_service = {service: key for key, service in service_by_key.items()}
    keys: list[str] = []
    services: list[str] = []
    for item in _items(raw):
        if item in service_by_key:
            key = item
            service = service_by_key[item]
        elif item in key_by_service:
            key = key_by_service[item]
            service = item
        else:
            valid = sorted(set(service_by_key) | set(key_by_service))
            raise SystemExit(
                f"unknown deferred runtime: {item}; valid values: {', '.join(valid)}"
            )
        if key not in keys:
            keys.append(key)
            services.append(service)
    return keys, services


def apply_deferred_state(
    path: Path,
    keys: list[str],
    *,
    reason: str,
    source: str,
    now: float | None = None,
) -> None:
    # runtime-state.json은 이미 떠있는 Gateway 프로세스가 실시간으로 읽는 파일이다.
    # 임시 파일에 쓰고 os.replace로 교체하는 이유는 그 프로세스가 절대 잘리다 만
    # JSON을 읽지 않게 하기 위함이다(RuntimeStateStore/MainModelStateStore와
    # 동일한 원자적 쓰기 패턴).
    if not keys:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
    states = payload.get("states") if isinstance(payload, dict) else None
    if not isinstance(states, dict):
        states = {}
    timestamp = time.time() if now is None else now
    for key in keys:
        states[key] = {
            "state": "stopped",
            "reason": reason,
            "source": source,
            "updated_at": timestamp,
        }
    next_payload = {"schema_version": 2, "states": states}
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(next_payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve deferred runtimes and optionally stamp Gateway runtime-state."
    )
    parser.add_argument("--config-root", type=Path, default=Path.cwd())
    parser.add_argument("--compose-file", type=Path, default=Path("ops/compose/full-stack.private-network.yaml"))
    parser.add_argument("--runtimes", default="")
    parser.add_argument("--profile", default="")
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--apply-state", action="store_true")
    parser.add_argument("--reason", default="deferred_at_deploy")
    parser.add_argument("--source", default="deploy")
    parser.add_argument("--output", choices=("lines", "json"), default="lines")
    args = parser.parse_args()

    compose_path = args.compose_file
    if not compose_path.is_absolute():
        compose_path = args.config_root / compose_path
    topology = load_runtime_topology(args.config_root, compose_path=compose_path)
    raw_runtimes = args.runtimes
    if not raw_runtimes and args.profile:
        raw_runtimes = ",".join(load_deploy_profile_runtimes(args.config_root, args.profile))
    keys, services = resolve_deferred_runtimes(topology, raw_runtimes)
    if args.apply_state:
        if args.state_path is None:
            raise SystemExit("--state-path is required with --apply-state")
        apply_deferred_state(
            args.state_path,
            keys,
            reason=args.reason,
            source=args.source,
        )
    if args.output == "json":
        print(json.dumps({"keys": keys, "services": services, "profile": args.profile}, ensure_ascii=False))
    else:
        print(" ".join(keys))
        print(" ".join(services))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
