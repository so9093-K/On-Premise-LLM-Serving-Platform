from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Any

from .settings import AppSettings


@dataclass(frozen=True)
class RuntimeConfigurationSnapshot:
    """요청 처리 중 안전하게 교체할 수 있는 운영 설정 snapshot.

    ``AppSettings``는 deployment/runtime identity 같은 부팅 시점 계약을 계속
    소유한다. 이 snapshot에는 Control Plane이 향후 operator override로 변경할
    값 중, 이미 생성된 RuntimeClient나 별도 프로세스를 재구성하지 않고 Gateway
    요청 경로가 즉시 다시 읽을 수 있는 값만 둔다.
    """

    revision: int
    max_retrieval_documents: int
    streaming_max_duration_seconds: float
    streaming_max_chunks: int
    streaming_max_bytes: int

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("runtime configuration revision must be a non-negative integer")
        if (
            isinstance(self.max_retrieval_documents, bool)
            or not isinstance(self.max_retrieval_documents, int)
            or self.max_retrieval_documents < 1
        ):
            # Public retrieval contract의 실제 maxItems는 계약/schema가 소유한다.
            # provider가 같은 숫자를 복제하면 두 SoT가 생기므로 여기서는 snapshot의
            # 구조적 유효성(양수 integer)만 보장한다.
            raise ValueError("max_retrieval_documents must be a positive integer")
        if (
            isinstance(self.streaming_max_duration_seconds, bool)
            or not isinstance(self.streaming_max_duration_seconds, (int, float))
            or float(self.streaming_max_duration_seconds) <= 0
        ):
            raise ValueError("streaming_max_duration_seconds must be greater than 0")
        if (
            isinstance(self.streaming_max_chunks, bool)
            or not isinstance(self.streaming_max_chunks, int)
            or self.streaming_max_chunks < 1
        ):
            raise ValueError("streaming_max_chunks must be a positive integer")
        if (
            isinstance(self.streaming_max_bytes, bool)
            or not isinstance(self.streaming_max_bytes, int)
            or self.streaming_max_bytes < 1
        ):
            raise ValueError("streaming_max_bytes must be a positive integer")


_RUNTIME_FIELDS = frozenset(
    {
        "max_retrieval_documents",
        "streaming_max_duration_seconds",
        "streaming_max_chunks",
        "streaming_max_bytes",
    }
)


class RuntimeConfigurationProvider:
    """Gateway-local hot-reload 설정의 원자적 snapshot provider.

    읽는 쪽은 frozen snapshot 하나를 받아 요청 전체에서 같은 revision을 사용할 수
    있고, 갱신은 새 snapshot으로 한 번에 교체한다. 파일 persistence와 operator
    revision/history는 Configuration Plane store의 책임이며 이 객체는 런타임 적용
    경계만 소유한다.
    """

    def __init__(self, initial: RuntimeConfigurationSnapshot) -> None:
        self._lock = RLock()
        self._snapshot = initial

    @classmethod
    def from_settings(cls, settings: AppSettings) -> RuntimeConfigurationProvider:
        return cls(
            RuntimeConfigurationSnapshot(
                revision=0,
                max_retrieval_documents=settings.max_retrieval_documents,
                streaming_max_duration_seconds=settings.streaming_max_duration_seconds,
                streaming_max_chunks=settings.streaming_max_chunks,
                streaming_max_bytes=settings.streaming_max_bytes,
            )
        )

    def snapshot(self) -> RuntimeConfigurationSnapshot:
        with self._lock:
            return self._snapshot

    def update(self, **changes: Any) -> RuntimeConfigurationSnapshot:
        unknown = set(changes) - _RUNTIME_FIELDS
        if unknown:
            raise ValueError(
                "unknown runtime configuration fields: " + ", ".join(sorted(unknown))
            )
        with self._lock:
            candidate = replace(
                self._snapshot,
                revision=self._snapshot.revision + 1,
                **changes,
            )
            self._snapshot = candidate
            return candidate

    def install(self, snapshot: RuntimeConfigurationSnapshot) -> RuntimeConfigurationSnapshot:
        """Persistence layer가 검증한 revision snapshot을 그대로 설치한다."""
        with self._lock:
            current = self._snapshot
            if snapshot.revision < current.revision:
                raise ValueError("runtime configuration revision cannot move backwards")
            if snapshot.revision == current.revision:
                if snapshot != current:
                    raise ValueError(
                        "runtime configuration values cannot change without a new revision"
                    )
                return current
            self._snapshot = snapshot
            return snapshot

    def settings_view(self, settings: AppSettings) -> RuntimeSettingsView:
        return RuntimeSettingsView(settings, self)


class RuntimeSettingsView:
    """기존 service 코드를 깨지 않고 mutable policy만 provider에서 읽는 view."""

    def __init__(self, settings: AppSettings, provider: RuntimeConfigurationProvider) -> None:
        self._settings = settings
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._settings, name)

    @property
    def max_retrieval_documents(self) -> int:
        return self._provider.snapshot().max_retrieval_documents

    @property
    def streaming_max_duration_seconds(self) -> float:
        return self._provider.snapshot().streaming_max_duration_seconds

    @property
    def streaming_max_chunks(self) -> int:
        return self._provider.snapshot().streaming_max_chunks

    @property
    def streaming_max_bytes(self) -> int:
        return self._provider.snapshot().streaming_max_bytes
