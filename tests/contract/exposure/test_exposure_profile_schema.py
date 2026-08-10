"""exposure profile validator의 구조적 보안 규칙을 테스트한다."""

from __future__ import annotations

from .helpers import *  # noqa: F401,F403


def test_resolve_exposure_mode_fails_on_unknown_mode() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.compose.resolve_exposure_mode import load_exposure_data, resolve
    import pytest
    data = load_exposure_data(ROOT)
    with pytest.raises(SystemExit) as exc_info:
        resolve("unsupported_mode", data)
    assert exc_info.value.code == 2


def test_exposure_validator_requires_service_categories() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import validate

    data, services = _category_validator_fixture()
    services["runtime_a"].pop("categories")

    violations = validate(data, services=services)

    assert any(".categories" in violation for violation in violations)


def test_exposure_validator_rejects_default_private_operations_category() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import validate

    data, services = _category_validator_fixture()
    data["profiles"]["private"]["host_published"].append("ops")

    violations = validate(data, services=services)

    assert any(
        "default_private profile must not host-publish operations_endpoint services" in violation
        for violation in violations
    )


def test_exposure_validator_requires_all_model_runtime_services_for_diagnostic_profile() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import validate

    data, services = _category_validator_fixture()
    data["profiles"]["diagnostic"]["host_published"].remove("runtime_b")

    violations = validate(data, services=services)

    assert any(
        "diagnostic_full_stack profile is missing model_runtime services" in violation
        for violation in violations
    )


def test_exposure_validator_requires_diagnostic_visualization_category() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import validate

    data, services = _category_validator_fixture()
    data["profiles"]["diagnostic"]["host_published"].remove("view")

    violations = validate(data, services=services)

    assert any(
        "diagnostic_full_stack profile must host-publish at least one visualization service" in violation
        for violation in violations
    )
