from __future__ import annotations

from .helpers import *  # noqa: F401,F403


def test_compose_restart_script_defaults_to_no_deps() -> None:
    script = (ROOT / "scripts/compose/compose_restart.sh").read_text(encoding="utf-8")
    assert "--no-deps" in script
    assert "WITH_DEPS" in script
    assert "compose_context_init" in script
    assert "resolve_exposure_mode.py" in script
    assert "render_main_model_boot_override.py" in script


def test_compose_restart_registered_in_makefile_and_registry() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "compose-restart:" in makefile
    assert "bash scripts/compose/compose_restart.sh" in makefile

    registry = (ROOT / "configs/command_registry.yaml").read_text(encoding="utf-8")
    assert "make_target: compose-restart" in registry
    assert "safety: starts_services" in registry
