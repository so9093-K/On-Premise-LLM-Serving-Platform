from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_validate_docs_exposure_passes_on_current_state() -> None:
    """validate_docs_exposure must pass on the current docs and features/ state."""
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_docs_exposure import validate
    violations = validate(ROOT)
    assert violations == [], (
        "validate_docs_exposure found violations in current docs/features:\n"
        + "\n".join(violations)
    )

