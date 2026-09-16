from __future__ import annotations

from scripts import platform_cli


def test_local_build_uses_shared_vllm_image_authority_when_legacy_risk_pin_differs(monkeypatch):
    local_id = "sha256:" + "a" * 64
    new_id = "sha256:" + "b" * 64
    risk_digest = "registry.example.com/risk@sha256:" + "c" * 64
    platform_digest = "registry.example.com/platform@sha256:" + "d" * 64
    commands: list[tuple[tuple[str, ...], dict[str, str] | None]] = []
    pinned: list[tuple[str, str]] = []

    monkeypatch.setattr(
        platform_cli,
        "_env_values",
        lambda: {
            "PLATFORM_IMAGE": platform_digest,
            "VLLM_IMAGE": local_id,
            "RISK_VLLM_IMAGE": risk_digest,
        },
    )
    monkeypatch.setattr(
        platform_cli,
        "_run",
        lambda *command, env=None: commands.append((command, env)),
    )
    monkeypatch.setattr(platform_cli, "resolve_local_image_id", lambda image: new_id)
    monkeypatch.setattr(
        platform_cli,
        "pin_matching_env_values",
        lambda _path, source, image_id, **_kwargs: pinned.append((source, image_id)) or ["VLLM_IMAGE"],
    )
    target = type("Target", (), {"controllable": True, "target_id": "linux-nvidia-dynamic"})()

    platform_cli.build_target(target, no_cache=True)

    assert len(commands) == 1
    assert commands[0][0] == ("bash", "scripts/build/build_vllm_unified_image.sh")
    assert commands[0][1] is not None
    assert commands[0][1]["PROJECT_BUILD_NO_CACHE"] == "1"
    assert commands[0][1]["VLLM_UNIFIED_BUILD_IMAGE"].startswith("ai-model-serving-vllm-unified:")
    assert pinned == [(local_id, new_id)]
