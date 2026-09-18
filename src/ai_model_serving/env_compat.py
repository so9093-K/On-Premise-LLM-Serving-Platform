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
    canonical_present = canonical in mapping
    legacy_present = legacy in mapping
    canonical_value = mapping.get(canonical, "")
    legacy_value = mapping.get(legacy, "")

    if canonical_present and legacy_present and canonical_value != legacy_value:
        raise RuntimeError(
            f"conflicting env keys {legacy} and {canonical}; keep only {canonical}"
        )
    if canonical_present:
        return canonical_value
    if legacy_present:
        return legacy_value
    return default
