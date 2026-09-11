"""native MLX runtime의 수명주기 소유권과 진단 출력을 검증한다.

macOS에서 MLX-VLM은 컨테이너가 아니라 호스트 프로세스라 Compose의
`restart: unless-stopped`에 해당하는 장치가 없다. launchd supervisor가 그 자리를
채우는데, pid 파일 경로와 동시에 살아 있으면 같은 포트에 서버가 둘 뜨거나 한쪽이
내린 프로세스를 다른 쪽이 되살린다. 소유자가 언제나 하나임을 고정한다.
"""

from __future__ import annotations

import plistlib

import pytest

from scripts.runtime import macos_mlx_runtime as metal


@pytest.fixture
def supervised(monkeypatch, tmp_path):
    """launchd가 설치된 상태를 흉내내고 실제 launchctl 호출은 기록만 한다."""
    installed = tmp_path / "com.ai-model-serving.metal.plist"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(metal, "_installed_plist", lambda: installed)
    monkeypatch.setattr(metal, "SUPERVISOR_PLIST", tmp_path / "generated.plist")
    monkeypatch.setattr(metal, "NATIVE_LOG_DIR", tmp_path)
    monkeypatch.setattr(metal, "LOG_PATH", tmp_path / "runtime.log")
    monkeypatch.setattr(metal, "SUPERVISOR_LOG_PATH", tmp_path / "supervisor.log")
    monkeypatch.setattr(metal, "PID_PATH", tmp_path / "metal.pid")
    monkeypatch.setattr(metal, "_launchctl", lambda *args: calls.append(args))
    monkeypatch.setattr(metal, "_require_runtime_host", lambda config: tmp_path / "python")
    monkeypatch.setattr(metal, "_model_aliases", lambda config: (tmp_path / "models" / "local-main", tmp_path / "models" / "assistant"))
    return installed, calls


def test_supervisor_plist_takes_its_command_from_server_command(monkeypatch, supervised):
    """plist를 손으로 쓰면 model revision·port·venv 경로가 YAML에서 복제된다.

    실행 명령의 단일 기준은 server_command이고 plist는 그 결과를 감싸기만 한다.
    """
    installed, calls = supervised
    command = ["/venv/bin/mlx_vlm.server", "--model", "local-main", "--port", "9401"]
    monkeypatch.setattr(metal, "server_command", lambda config, *, listen_host: command)
    monkeypatch.setattr(metal, "_supervisor_loaded", lambda: False)
    monkeypatch.setattr(metal, "_await_readiness", lambda *a, **k: None)

    metal.install_supervisor({}, listen_host="0.0.0.0")

    document = plistlib.loads(installed.read_bytes())
    assert document["ProgramArguments"] == command
    assert document["Label"] == metal.SUPERVISOR_LABEL
    # Compose의 restart: unless-stopped 대응. 의도적 정지는 bootout이 담당한다.
    assert document["KeepAlive"] is True
    assert document["StandardOutPath"] == document["StandardErrorPath"]
    assert ("bootstrap", metal._launchctl_domain(), str(installed)) in calls


def test_install_refuses_while_a_pidfile_runtime_is_tracked(monkeypatch, supervised):
    """pid 경로 프로세스가 살아 있는데 launchd를 올리면 같은 포트에 둘이 뜬다."""
    monkeypatch.setattr(metal, "_tracked_pid", lambda: 4242)

    with pytest.raises(RuntimeError, match="run/metal.pid"):
        metal.install_supervisor({}, listen_host="0.0.0.0")


def test_installed_supervisor_owns_start_and_stop_instead_of_the_pid_file(monkeypatch, supervised):
    installed, calls = supervised
    installed.write_bytes(plistlib.dumps({"Label": metal.SUPERVISOR_LABEL}))
    monkeypatch.setattr(metal, "_supervisor_loaded", lambda: True)

    killed: list[int] = []
    monkeypatch.setattr(metal.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(metal, "_tracked_pid", lambda: 4242)

    metal.stop_background()

    # SIGTERM은 KeepAlive가 즉시 되살리므로 bootout만이 실제 정지다.
    assert killed == []
    assert ("bootout", metal._supervisor_job()) in calls

    launched: list[object] = []
    monkeypatch.setattr(metal.subprocess, "Popen", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(metal, "_health_payload", lambda config: '{"status":"ok"}')

    metal.start_background({}, listen_host="0.0.0.0")

    assert launched == []
    assert not (tmp_pid := metal.PID_PATH).exists(), f"{tmp_pid} must stay unused under launchd"


def test_log_tail_reads_from_the_end_of_an_unbounded_log(monkeypatch, tmp_path):
    """이 로그는 회전이 없는 append 파일이라 전체를 읽어 자를 수 없다."""
    log = tmp_path / "metal.log"
    filler = "x" * 1000
    log.write_text("\n".join(f"{filler}{index}" for index in range(500)), encoding="utf-8")
    monkeypatch.setattr(metal, "LOG_PATH", log)

    lines = metal._log_tail()

    assert len(lines) == metal._LOG_TAIL_LINES
    assert lines[-1].endswith("499")


def test_log_tail_is_silent_when_the_runtime_never_wrote_a_log(monkeypatch, tmp_path):
    monkeypatch.setattr(metal, "LOG_PATH", tmp_path / "missing.log")
    assert metal._log_tail() == []


def test_each_lifecycle_owner_writes_its_own_log_file(monkeypatch, supervised):
    """로그 파일을 공유하면 "누가 먼저 만들었는가"가 동작을 가른다.

    TCC 보호 경로(~/Desktop 등)에서 launchd는 자기가 만들어 com.apple.macl이 붙은
    파일만 열 수 있다. `make up`의 pid 경로가 먼저 로그를 만들어 둔 뒤 supervisor를
    설치하면 job이 EX_CONFIG(78)로 죽으면서 로그를 한 줄도 남기지 않았다. 실제로
    재현하고 원인까지 확인한 결함이며, 경로를 소유자별로 나눠 그 상태를 없앴다.
    """
    installed, calls = supervised
    monkeypatch.setattr(metal, "server_command", lambda config, *, listen_host: ["/venv/bin/server"])
    monkeypatch.setattr(metal, "_supervisor_loaded", lambda: False)
    monkeypatch.setattr(metal, "_await_readiness", lambda *a, **k: None)

    metal.install_supervisor({}, listen_host="0.0.0.0")

    document = plistlib.loads(installed.read_bytes())
    assert document["StandardOutPath"] == str(metal.SUPERVISOR_LOG_PATH)
    assert document["StandardOutPath"] != str(metal.LOG_PATH), "pid 경로와 파일을 공유하면 안 된다"


def test_diagnostics_read_the_log_of_whoever_owns_the_runtime(monkeypatch, supervised):
    installed, _ = supervised
    metal.LOG_PATH.write_text("pid 경로가 남긴 줄\n", encoding="utf-8")
    metal.SUPERVISOR_LOG_PATH.write_text("launchd가 남긴 줄\n", encoding="utf-8")

    assert metal._log_tail() == ["pid 경로가 남긴 줄"]

    installed.write_bytes(plistlib.dumps({"Label": metal.SUPERVISOR_LABEL}))
    assert metal._log_tail() == ["launchd가 남긴 줄"]
