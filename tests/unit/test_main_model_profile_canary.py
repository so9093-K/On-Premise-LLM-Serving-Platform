"""scripts/models/check_main_model_profiles.py가 카탈로그의 모든 프로필을
빠짐없이 순회하며 canary 명령을 실행하는지(프로필 하나를 빠뜨리지 않는지)
검증한다."""

from __future__ import annotations

from pathlib import Path

from scripts.models import check_main_model_profiles

ROOT = Path(__file__).resolve().parents[2]


def test_canary_iterates_catalog_profiles_without_embedded_profile_ids(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(command, check):
        assert check is True
        calls.append(command)

    monkeypatch.setattr(check_main_model_profiles.subprocess, "run", fake_run)
    assert (
        check_main_model_profiles.main(
            ["--catalog", str(ROOT / "configs/main_model_profiles.yaml")]
        )
        == 0
    )

    catalog = check_main_model_profiles.load_main_model_catalog(
        ROOT / "configs/main_model_profiles.yaml"
    )
    assert len(calls) == len(catalog.profiles)
    identities = {
        (command[command.index("--model") + 1], command[command.index("--revision") + 1])
        for command in calls
    }
    assert identities == {
        (profile.model_id, profile.revision) for profile in catalog.profiles.values()
    }
