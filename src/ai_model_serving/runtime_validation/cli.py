from __future__ import annotations

import argparse
from pathlib import Path

from .config import DEFAULT_BASE_URLS, load_runtime_config
from .validator import RuntimeValidator

ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="live runtime validation을 실행하고 JSON/Markdown report를 생성합니다.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output-dir", default="reports/runtime")
    parser.add_argument("--gateway-base", default=None, help=f"Gateway base URL입니다. 우선순위: CLI > 환경변수 GATEWAY_BASE_URL > 기본값 {DEFAULT_BASE_URLS['gateway']}.")
    parser.add_argument("--risk-base", default=None, help=f"Risk Adapter base URL입니다. 우선순위: CLI > 환경변수 RISK_ADAPTER_BASE_URL > 기본값 {DEFAULT_BASE_URLS['risk']}.")
    parser.add_argument("--main-llm-base", default=None, help="main LLM vLLM OpenAI-compatible base URL입니다. 우선순위: CLI > MAIN_LLM_BASE_URL > config port 기본값.")
    parser.add_argument("--embedding-base", default=None, help="embedding vLLM OpenAI-compatible base URL입니다. 우선순위: CLI > EMBEDDING_BASE_URL > config port 기본값.")
    parser.add_argument("--embedding-ko-base", default=None, help="embedding-ko vLLM OpenAI-compatible base URL입니다. 우선순위: CLI > EMBEDDING_KO_BASE_URL > config port 기본값.")
    parser.add_argument("--risk-prompt-base", default=None, help="risk-prompt vLLM OpenAI-compatible base URL입니다. 우선순위: CLI > RISK_PROMPT_BASE_URL > config port 기본값.")
    parser.add_argument("--risk-siren-base", default=None, help="retired risk-siren 호환 옵션입니다. enabled runtime일 때만 사용됩니다.")
    parser.add_argument("--prometheus-base", default=None, help=f"Prometheus base URL입니다. 우선순위: CLI > PROMETHEUS_BASE_URL > 기본값 {DEFAULT_BASE_URLS['prometheus']}.")
    parser.add_argument("--grafana-base", default=None, help=f"Grafana base URL입니다. 우선순위: CLI > GRAFANA_BASE_URL > 기본값 {DEFAULT_BASE_URLS['grafana']}.")
    parser.add_argument("--grafana-user", default="", help="Grafana API 기본 인증 사용자입니다. 기본값은 GRAFANA_ADMIN_USER 또는 admin입니다.")
    parser.add_argument("--grafana-password", default="", help="Grafana API 기본 인증 비밀번호입니다. 기본값은 GRAFANA_ADMIN_PASSWORD 또는 admin입니다.")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--admin-api-key", default="")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--soak-seconds", type=int, default=1800)
    parser.add_argument("--soak-interval-seconds", type=float, default=1.0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--skip-soak", action="store_true")
    parser.add_argument("--config-only", action="store_true", help="live service를 호출하지 않고 harness 설정만 검증합니다.")
    parser.add_argument("--allow-failures", action="store_true", help="live check가 실패해도 report를 기록하고 exit code 0으로 종료합니다.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_runtime_config(args)
    validator = RuntimeValidator(config)
    if config.config_only:
        validator.run_config_only()
    else:
        validator.run_live()
    json_path, md_path = validator.write_reports()
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    failed = [item for item in validator.results if not item.passed]
    return 0 if config.allow_failures or not failed else 1
