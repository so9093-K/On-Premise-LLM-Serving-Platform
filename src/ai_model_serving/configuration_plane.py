"""Read-only Configuration Plane projection.

The projection deliberately reads resolved ``AppSettings`` rather than raw
environment files.  It never serializes secret values; a future mutable
operator store will be added behind this boundary.
"""

from __future__ import annotations

from .configuration import load_yaml_mapping
from .project_paths import resolve_project_root


def _schema_items() -> list[dict[str, Any]]:
    document = load_yaml_mapping(resolve_project_root() / "configs" / "configuration_schema.yaml")
    items = document.get("items")
    if document.get("version") != 1 or not isinstance(items, list):
        raise RuntimeError("configuration_schema.yaml must declare version 1 and items")
    if not all(isinstance(item, dict) for item in items):
        raise RuntimeError("configuration_schema.yaml items must be mappings")
    return [dict(item) for item in items]


def _resolve_setting(settings: Any, path: str) -> Any:
    current: Any = settings
    for segment in path.split("."):
        current = current[segment] if isinstance(current, dict) else getattr(current, segment)
    return current


def configuration_schema() -> dict[str, Any]:
    return {"version": 1, "items": _schema_items()}


def effective_configuration(settings: Any) -> dict[str, Any]:
    """Return metadata-aligned effective values without serializing secrets."""
    items: list[dict[str, Any]] = []
    for schema in _schema_items():
        raw_value = _resolve_setting(settings, str(schema["setting_path"]))
        secret = bool(schema["sensitive"])
        value = None if secret else (sorted(raw_value) if isinstance(raw_value, (set, frozenset)) else raw_value)
        item = {
            "key": schema["key"],
            "effective_value": value,
            "effective_source": schema["effective_source"],
            "owner": schema["owner"],
            "sensitive": secret,
        }
        if secret:
            item["configured"] = bool(raw_value)
        items.append(item)
    return {"version": 1, "items": items}
