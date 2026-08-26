from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_runtime_config
from .validator import RuntimeValidator

def _find_project_root() -> Path:
    """VERSION+configs 마커로 저장소 루트를 찾는다.

    예전엔 parents[3]로 깊이를 박아뒀는데, 그러면 이 패키지가 다른 위치로 옮겨질 때
    조용히 엉뚱한 디렉터리를 루트로 잡는다.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "VERSION").exists() and (parent / "configs").exists():
            return parent
    raise RuntimeError("could not locate project root from runtime validation package")


ROOT = _find_project_root()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="live runtime validation을 실행하고 JSON/Markdown report를 생성합니다.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output-dir", default="reports/runtime")
    parser.add_argument("--gateway-base", default=None, help="Gateway base URL입니다. 우선순위: CLI > RUNTIME_VALIDATION_GATEWAY_BASE_URL > publish 주소.")
    parser.add_argument("--risk-base", default=None, help="Risk Adapter base URL입니다. 우선순위: CLI > RUNTIME_VALIDATION_RISK_BASE_URL > publish 주소.")
    parser.add_argument("--main-llm-base", default=None, help="main LLM vLLM base URL입니다. 우선순위: CLI > RUNTIME_VALIDATION_MAIN_LLM_BASE_URL > publish 주소.")
    parser.add_argument("--embedding-base", default=None, help="embedding vLLM base URL입니다. 우선순위: CLI > RUNTIME_VALIDATION_EMBEDDING_BASE_URL > publish 주소.")
    parser.add_argument("--embedding-ko-base", default=None, help="embedding-ko vLLM base URL입니다. 우선순위: CLI > RUNTIME_VALIDATION_EMBEDDING_KO_BASE_URL > publish 주소.")
    parser.add_argument("--risk-prompt-base", default=None, help="risk-prompt vLLM base URL입니다. 우선순위: CLI > RUNTIME_VALIDATION_RISK_PROMPT_BASE_URL > publish 주소.")
    parser.add_argument("--prometheus-base", default=None, help="Prometheus base URL입니다. 우선순위: CLI > RUNTIME_VALIDATION_PROMETHEUS_BASE_URL > publish 주소.")
    parser.add_argument("--grafana-base", default=None, help="Grafana base URL입니다. 우선순위: CLI > RUNTIME_VALIDATION_GRAFANA_BASE_URL > publish 주소.")
    parser.add_argument("--grafana-user", default="", help="Grafana API 기본 인증 사용자입니다. 기본값은 GRAFANA_ADMIN_USER 또는 admin입니다.")
    parser.add_argument("--grafana-password", default="", help="Grafana API 기본 인증 비밀번호입니다. 기본값은 GRAFANA_ADMIN_PASSWORD 또는 admin입니다.")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--admin-api-key", default="")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--allow-failures", action="store_true", help="live check가 실패해도 report를 기록하고 exit code 0으로 종료합니다.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_runtime_config(args)
    validator = RuntimeValidator(config)
    validator.run_live()
    json_path, md_path = validator.write_reports()
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    failed = [item for item in validator.results if item.failed]
    return 0 if config.allow_failures or not failed else 1
