from __future__ import annotations

import subprocess
import sys

from .common import ROOT
from .filesystem import validate_json_and_yaml_parse
from .model_config import (
    validate_deployment_targets,
    validate_model_resource_control_policy,
    validate_ports,
    validate_risk_detector_generation_budget,
)
from .schemas import (
    validate_common_error_codes,
    validate_openapi_error_surface,
    validate_openapi_refs,
    validate_request_schemas,
    validate_risk_schema,
)
from .versioning import (
    validate_python_compatibility,
    validate_version_alignment,
)


def validate_vllm_compose_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/compose/validate_vllm_compose.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise SystemExit(output or "vLLM compose validation failed")


# validate_json_and_yaml_parse는 CHECKS에 넣지 않고 먼저 단독으로 돌린다 -- 아래
# 참고.
CHECKS = [
    validate_deployment_targets,
    validate_vllm_compose_contract,
    validate_version_alignment,
    validate_python_compatibility,
    validate_openapi_refs,
    validate_openapi_error_surface,
    validate_request_schemas,
    validate_risk_schema,
    validate_risk_detector_generation_budget,
    validate_ports,
    validate_common_error_codes,
    validate_model_resource_control_policy,
]


def main() -> None:
    # 문서 파싱은 나머지 check들의 선행 조건이다. 아래 check들은 전부 이 파일들을
    # 읽으므로, YAML 하나가 깨지면 각자 raw ParserError로 터진다 -- 그건 SystemExit이
    # 아니라서 수집되지 않고 traceback으로 전파되고, 그 과정에서 "어느 파일이
    # 깨졌는지" 알려주는 유일하게 읽을 만한 메시지가 묻힌다. 그래서 여기서 먼저 끊는다.
    validate_json_and_yaml_parse()

    # 여기서부터 각 check는 부작용 없는 순수 읽기+assert라 서로 독립적이다 -- 하나가
    # 실패해도 나머지를 계속 돌려서, 한 번의 위반이 다른 위반들을 가려버리지 않게 한다.
    # 예상 못 한(SystemExit이 아닌) 예외는 그 check 자체의 버그일 수 있으므로 그대로
    # 전파해 traceback을 보존한다.
    failures = []
    for check in CHECKS:
        try:
            check()
        except SystemExit as exc:
            failures.append(f'{check.__name__}: {exc.code}')
    if failures:
        for failure in failures:
            print(f'FAILED {failure}')
        raise SystemExit(f'{len(failures)} contract validation check(s) failed')
    print('contract validation completed')


if __name__ == '__main__':
    main()
