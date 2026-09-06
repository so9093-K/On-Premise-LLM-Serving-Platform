"""배포 시점에 GPU VRAM 예산이 빠듯할 때 embedding/embedding-ko/risk-prompt 같은
secondary 런타임을 처음부터 정지 상태로 둘지 정한다. defer를 빠뜨리면 main model이
부팅 중 GPU 메모리 부족으로 기동을 실패할 수 있고, 반대로 잘못 defer하면 배포
직후부터 해당 엔드포인트가 이유 없이 503을 낸다. scripts/ci/deploy_gitlab_compose.sh가
실제 배포 시퀀스에서 이 스크립트를 하드 게이트로 호출하므로, 여기서 실패하면
배포 자체가 중단되고 env가 롤백된다.

이 스크립트는 결정만 내리고 Gateway의 runtime-state.json은 쓰지 않는다. 그 파일의
writer는 Gateway 하나다 -- 배포 사용자와 컨테이너가 같은 디렉터리를 함께 쓰면 먼저
만든 쪽이 소유권을 가져가 반대쪽이 영구히 쓰지 못하기 때문이다. 결정은 env로
전달되고 기록은 Gateway가 한다(services/runtime_state.py 참고)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.runtime_topology import RuntimeTopology, load_runtime_topology


def _items(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_deploy_profile(config_root: Path, profile: str) -> tuple[str, list[str]]:
    # --runtimes를 배포마다 손으로 나열하는 대신, configs/deploy_profiles.yaml에
    # 미리 정의해둔 조합(예: GPU가 작은 호스트용 프로필)을 이름으로 재사용하기 위함.
    path = config_root / "configs/deploy_profiles.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise SystemExit("configs/deploy_profiles.yaml must define profiles")
    effective_profile = profile or data.get("default_profile")
    if not isinstance(effective_profile, str) or not effective_profile:
        raise SystemExit("configs/deploy_profiles.yaml must define default_profile")
    item = profiles.get(effective_profile)
    if not isinstance(item, dict):
        valid = ", ".join(sorted(str(key) for key in profiles))
        raise SystemExit(
            f"unknown deploy runtime profile: {effective_profile}; valid values: {valid}"
        )
    runtimes = item.get("deferred_runtimes", [])
    if not isinstance(runtimes, list) or not all(isinstance(value, str) for value in runtimes):
        raise SystemExit(
            f"deploy profile {effective_profile} must define deferred_runtimes as a string list"
        )
    return effective_profile, runtimes


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the deferred runtime set for a deploy."
    )
    parser.add_argument("--config-root", type=Path, default=Path.cwd())
    parser.add_argument("--compose-file", type=Path, default=Path("ops/compose/full-stack.private-network.yaml"))
    parser.add_argument("--runtimes", default="")
    parser.add_argument("--profile", default="")
    parser.add_argument("--output", choices=("lines", "json"), default="lines")
    args = parser.parse_args()

    compose_path = args.compose_file
    if not compose_path.is_absolute():
        compose_path = args.config_root / compose_path
    topology = load_runtime_topology(args.config_root, compose_path=compose_path)
    raw_runtimes = args.runtimes
    effective_profile = ""
    if not raw_runtimes:
        effective_profile, profile_runtimes = load_deploy_profile(
            args.config_root, args.profile
        )
        raw_runtimes = ",".join(profile_runtimes)
    keys, services = resolve_deferred_runtimes(topology, raw_runtimes)
    if args.output == "json":
        print(
            json.dumps(
                {"keys": keys, "services": services, "profile": effective_profile},
                ensure_ascii=False,
            )
        )
    else:
        print(" ".join(keys))
        print(" ".join(services))
        print(effective_profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
