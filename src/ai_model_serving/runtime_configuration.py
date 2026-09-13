from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeConfigurationSnapshot:
    """Request-path operational policy that may change without rebuilding the app.

    ``AppSettings`` remains the immutable startup/deployment snapshot.  Only values
    whose consumers can safely read a fresh value per request/operation belong here.
    A future operator store can therefore swap one validated snapshot atomically
    instead of mutating ``AppSettings`` in place.
    """

    revision: int = 0
    gateway_timeout_seconds: float = 125.0
    max_retrieval_documents: int = 32
    streaming_max_duration_seconds: float = 300.0
    streaming_max_chunks: int = 20_000
    streaming_max_bytes: int = 104_857_600

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("runtime configuration revision must be non-negative")
        if self.gateway_timeout_seconds <= 0:
            raise ValueError("gateway_timeout_seconds must be greater than zero")
        if self.max_retrieval_documents < 1:
            raise ValueError("max_retrieval_documents must be at least 1")
        if self.streaming_max_duration_seconds <= 0:
            raise ValueError("streaming_max_duration_seconds must be greater than zero")
        if self.streaming_max_chunks < 1:
            raise ValueError("streaming_max_chunks must be at least 1")
        if self.streaming_max_bytes < 1:
            raise ValueError("streaming_max_bytes must be at least 1")

    @classmethod
    def from_settings(cls, settings: Any) -> "RuntimeConfigurationSnapshot":
        return cls(
            gateway_timeout_seconds=float(settings.gateway_timeout_seconds),
            max_retrieval_documents=int(settings.max_retrieval_documents),
            streaming_max_duration_seconds=float(settings.streaming_max_duration_seconds),
            streaming_max_chunks=int(settings.streaming_max_chunks),
            streaming_max_bytes=int(settings.streaming_max_bytes),
        )


class RuntimeConfigurationProvider:
    """Atomically exposes the current immutable runtime-policy snapshot.

    Reads are deliberately synchronous and tiny: request paths only copy one object
    reference while writes replace the complete validated snapshot under a lock.
    This keeps partially-applied configuration states unobservable.
    """

    def __init__(self, initial: RuntimeConfigurationSnapshot) -> None:
        self._snapshot = initial
        self._lock = RLock()

    @classmethod
    def from_settings(cls, settings: Any) -> "RuntimeConfigurationProvider":
        return cls(RuntimeConfigurationSnapshot.from_settings(settings))

    def snapshot(self) -> RuntimeConfigurationSnapshot:
        with self._lock:
            return self._snapshot

    def replace(
        self,
        *,
        revision: int | None = None,
        gateway_timeout_seconds: float | None = None,
        max_retrieval_documents: int | None = None,
        streaming_max_duration_seconds: float | None = None,
        streaming_max_chunks: int | None = None,
        streaming_max_bytes: int | None = None,
    ) -> RuntimeConfigurationSnapshot:
        with self._lock:
            current = self._snapshot
            next_snapshot = replace(
                current,
                revision=current.revision + 1 if revision is None else revision,
                gateway_timeout_seconds=(
                    current.gateway_timeout_seconds
                    if gateway_timeout_seconds is None
                    else float(gateway_timeout_seconds)
                ),
                max_retrieval_documents=(
                    current.max_retrieval_documents
                    if max_retrieval_documents is None
                    else int(max_retrieval_documents)
                ),
                streaming_max_duration_seconds=(
                    current.streaming_max_duration_seconds
                    if streaming_max_duration_seconds is None
                    else float(streaming_max_duration_seconds)
                ),
                streaming_max_chunks=(
                    current.streaming_max_chunks
                    if streaming_max_chunks is None
                    else int(streaming_max_chunks)
                ),
                streaming_max_bytes=(
                    current.streaming_max_bytes
                    if streaming_max_bytes is None
                    else int(streaming_max_bytes)
                ),
            )
            self._snapshot = next_snapshot
            return next_snapshot


_DYNAMIC_FIELDS = frozenset(
    {
        "gateway_timeout_seconds",
        "max_retrieval_documents",
        "streaming_max_duration_seconds",
        "streaming_max_chunks",
        "streaming_max_bytes",
    }
)


class RuntimeConfigurationSettingsView:
    """Read-only settings view that overlays request-path policy on AppSettings.

    Existing services intentionally keep using attribute access such as
    ``settings.streaming_max_chunks``.  Passing this view lets those consumers read
    the current snapshot without teaching every service about persistence or the
    future Configuration Plane mutation protocol.
    """

    def __init__(self, base_settings: Any, provider: RuntimeConfigurationProvider) -> None:
        self._base_settings = base_settings
        self._provider = provider

    @property
    def base_settings(self) -> Any:
        return self._base_settings

    @property
    def runtime_configuration(self) -> RuntimeConfigurationSnapshot:
        return self._provider.snapshot()

    def __getattr__(self, name: str) -> Any:
        if name in _DYNAMIC_FIELDS:
            return getattr(self._provider.snapshot(), name)
        return getattr(self._base_settings, name)
