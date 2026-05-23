from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ai_model_serving"


def _python_files(relative_dir: str) -> list[Path]:
    return sorted((SRC / relative_dir).rglob("*.py"))


def _module_name(path: Path) -> str:
    relative_path = path.relative_to(SRC).with_suffix("")
    parts = list(relative_path.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("ai_model_serving", *parts))


def _import_from_target(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""

    package_parts = _module_name(path).split(".")
    if path.name != "__init__.py":
        package_parts.pop()
    keep = len(package_parts) - node.level + 1
    prefix = package_parts[: max(keep, 0)]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = _import_from_target(path, node)
            if target:
                imports.append(target)
                imports.extend(f"{target}.{alias.name}" for alias in node.names if alias.name != "*")
    return imports


def _is_forbidden(target: str, forbidden_prefixes: tuple[str, ...]) -> bool:
    return any(target == prefix or target.startswith(f"{prefix}.") for prefix in forbidden_prefixes)


def _assert_no_imports(relative_dir: str, forbidden_prefixes: tuple[str, ...]) -> None:
    violations: list[str] = []
    for path in _python_files(relative_dir):
        for target in _imports(path):
            if _is_forbidden(target, forbidden_prefixes):
                violations.append(f"{path.relative_to(ROOT)} imports {target}")

    assert not violations, "Architecture boundary violations:\n" + "\n".join(violations)


def test_domain_does_not_import_runtime_layers() -> None:
    _assert_no_imports(
        "domain",
        (
            "fastapi",
            "httpx",
            "ai_model_serving.apps",
            "ai_model_serving.api",
            "ai_model_serving.services",
        ),
    )


def test_contracts_do_not_import_runtime_layers_or_settings() -> None:
    _assert_no_imports(
        "contracts",
        (
            "fastapi",
            "ai_model_serving.apps",
            "ai_model_serving.api",
            "ai_model_serving.services",
            "ai_model_serving.settings",
        ),
    )


def test_services_do_not_import_framework_or_upstream_clients() -> None:
    _assert_no_imports("services", ("fastapi", "ai_model_serving.upstream"))


def test_api_routers_do_not_import_upstream_clients() -> None:
    _assert_no_imports("api/routers", ("ai_model_serving.upstream",))
