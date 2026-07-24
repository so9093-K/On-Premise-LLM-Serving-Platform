from __future__ import annotations

from .docs_ops import (
    validate_api_contract_matrix,
    validate_build_ux_roles,
    validate_command_terminology_policy,
    validate_doc_invariants,
    validate_health_readiness_contract_docs,
    validate_internal_service_auth_contract,
    validate_monitoring_reference,
    validate_monitoring_resource_mapping,
    validate_multimodal_chat_and_token_caps,
    validate_operational_hardening_contract,
    validate_ops_templates,
    validate_retired_source_cleanup_policy,
    validate_runtime_validation_yaml,
    validate_status_vocabulary,
    validate_storage_path_management,
)
from .filesystem import validate_json_and_yaml_parse, validate_required_files
from .model_config import (
    validate_model_cards,
    validate_model_contracts_cross_reference,
    validate_model_lifecycle_docs_and_cli,
    validate_model_list_schema_enums,
    validate_model_registry_alignment,
    validate_model_resource_control_policy,
    validate_model_source_facts,
    validate_ports,
    validate_resource_requirements_doc,
    validate_risk_detector_generation_budget,
    validate_smoke_thresholds,
)
from .release_runtime import (
    validate_deployment_reproducibility,
    validate_implementation_reference,
    validate_mock_scope,
    validate_pytest_stability_config,
    validate_release_hygiene,
    validate_runtime_validation_harness,
    validate_risk_vllm_patch_lifecycle,
    validate_vllm_compose_contract,
)
from .schemas import (
    validate_common_error_codes,
    validate_generated_openapi_contract_schemas,
    validate_openapi_error_surface,
    validate_openapi_refs,
    validate_request_schemas,
    validate_risk_schema,
)
from .versioning import (
    validate_python_compatibility,
    validate_version_alignment,
    validate_version_bump_policy,
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
    validate_monitoring_reference,
    validate_model_source_facts,
    validate_version_bump_policy,
    validate_doc_invariants,
    validate_mock_scope,
    validate_model_list_schema_enums,
    validate_model_registry_alignment,
    validate_status_vocabulary,
    validate_pytest_stability_config,
    validate_resource_requirements_doc,
    validate_ops_templates,
    validate_runtime_validation_yaml,
    validate_health_readiness_contract_docs,
    validate_common_error_codes,
    validate_generated_openapi_contract_schemas,
    validate_api_contract_matrix,
    validate_model_contracts_cross_reference,
    validate_smoke_thresholds,
    validate_runtime_validation_harness,
    validate_retired_source_cleanup_policy,
    validate_build_ux_roles,
    validate_command_terminology_policy,
    validate_implementation_reference,
    validate_release_hygiene,
    validate_model_resource_control_policy,
    validate_internal_service_auth_contract,
    validate_multimodal_chat_and_token_caps,
    validate_operational_hardening_contract,
    validate_monitoring_resource_mapping,
    validate_storage_path_management,
    validate_model_lifecycle_docs_and_cli,
    validate_deployment_reproducibility,
    validate_risk_vllm_patch_lifecycle,
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
