from __future__ import annotations

from collections.abc import Mapping


def renamed_env_value(
    mapping: Mapping[str, str],
    canonical: str,
    legacy: str,
    default: str = "",
) -> str:
    """Resolve one renamed env key pair with fail-closed conflict handling.

    Canonical values win only when the legacy alias is absent or equal. During the
    compatibility window, a deployment that supplies both names with different
    values is ambiguous and must fail instead of silently selecting one.
    """
    canonical_value = mapping.get(canonical, "")
    legacy_value = mapping.get(legacy, "")

    if canonical_value and legacy_value and canonical_value != legacy_value:
        raise RuntimeError(
            f"conflicting env keys {legacy} and {canonical}; keep only {canonical}"
        )
    if canonical_value:
        return canonical_value
    if legacy_value:
        return legacy_value
    if canonical in mapping:
        return canonical_value
    if legacy in mapping:
        return legacy_value
    return default
