from __future__ import annotations

from .filesystem import validate_json_and_yaml_parse, validate_required_files
from .model_config import (
    validate_model_cards,
    validate_model_contracts_cross_reference,
    validate_model_list_schema_enums,
    validate_model_registry_alignment,
    validate_model_resource_control_policy,
    validate_model_source_facts,
    validate_ports,
    validate_risk_detector_generation_budget,
)
from .release_runtime import validate_vllm_compose_contract
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


CHECKS = [
    validate_required_files,
    validate_json_and_yaml_parse,
    validate_vllm_compose_contract,
    validate_version_alignment,
    validate_python_compatibility,
    validate_openapi_refs,
    validate_openapi_error_surface,
    validate_request_schemas,
    validate_risk_schema,
    validate_risk_detector_generation_budget,
    validate_ports,
    validate_model_cards,
    validate_model_source_facts,
    validate_model_list_schema_enums,
    validate_model_registry_alignment,
    validate_common_error_codes,
    validate_model_contracts_cross_reference,
    validate_model_resource_control_policy,
]


def main() -> None:
    # 각 check는 부작용 없는 순수 읽기+assert라 서로 독립적이다 -- 하나가 실패해도
    # 나머지를 계속 돌려서, 한 번의 위반이 다른 위반들을 가려버리지 않게 한다.
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
