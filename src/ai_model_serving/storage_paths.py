from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class StoragePath:
    key: str
    path: str
    kind: str
    owner: str
    lifecycle: str
    cleanup: str
    release_package: str
    env: str | None = None
    container_path: str | None = None
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "path": self.path,
            "kind": self.kind,
            "owner": self.owner,
            "lifecycle": self.lifecycle,
            "cleanup": self.cleanup,
            "release_package": self.release_package,
        }
        if self.env:
            payload["env"] = self.env
        if self.container_path:
            payload["container_path"] = self.container_path
        if self.notes:
            payload["notes"] = self.notes
        return payload


@dataclass(frozen=True)
class StorageRegistry:
    document: dict[str, Any]

    @classmethod
    def from_yaml(cls, path: Path) -> "StorageRegistry":
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))

    def paths(self) -> tuple[StoragePath, ...]:
        rows: list[StoragePath] = []
        for key, item in self.document.get("paths", {}).items():
            rows.append(
                StoragePath(
                    key=str(key),
                    path=str(item["path"]),
                    kind=str(item.get("kind", "unspecified")),
                    owner=str(item.get("owner", "unspecified")),
                    lifecycle=str(item.get("lifecycle", "unspecified")),
                    cleanup=str(item.get("cleanup", "unspecified")),
                    release_package=str(item.get("release_package", "unspecified")),
                    env=str(item["env"]) if item.get("env") else None,
                    container_path=str(item["container_path"]) if item.get("container_path") else None,
                    notes=str(item["notes"]) if item.get("notes") else None,
                )
            )
        return tuple(rows)

    def cleanup_flags(self) -> dict[str, Any]:
        return dict(self.document.get("cleanup_flags", {}))

    def external_host_paths(self) -> dict[str, Any]:
        return dict(self.document.get("external_host_paths", {}))

    def as_report_document(self) -> dict[str, Any]:
        return {
            "version": self.document.get("version"),
            "source_of_truth": self.document.get("source_of_truth", "configs/storage_paths.yaml"),
            "policy": self.document.get("policy", {}),
            "paths": [path.as_dict() for path in self.paths()],
            "external_host_paths": self.external_host_paths(),
            "cleanup_flags": self.cleanup_flags(),
        }


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")).replace("|", "/") for column in columns) + " |")
    return lines


def render_storage_report_markdown(document: dict[str, Any]) -> str:
    lines = [
        "# 로컬 저장소 경로 리포트",
        "",
        f"원천 파일: `{document['source_of_truth']}`",
        "",
        "이 리포트는 로컬 설정, 런타임 secret, 로그, report, 모델 캐시, build artifact가 어디에 생성되고 어떻게 정리되는지 한 곳에 요약한다.",
        "",
        "## 관리 대상 로컬 경로",
        "",
    ]
    lines.extend(
        _markdown_table(
            document["paths"],
            ["key", "path", "env", "container_path", "kind", "cleanup", "release_package"],
        )
    )
    lines.extend(["", "## 정리 플래그", ""])
    flag_rows = [
        {"flag": key, "default": value.get("default", ""), "deletes": ", ".join(value.get("deletes", []))}
        for key, value in document.get("cleanup_flags", {}).items()
    ]
    lines.extend(_markdown_table(flag_rows, ["flag", "default", "deletes"]))
    lines.extend(["", "## 외부 호스트 경로", ""])
    external_rows = [
        {"key": key, **value}
        for key, value in document.get("external_host_paths", {}).items()
    ]
    lines.extend(_markdown_table(external_rows, ["key", "path", "mount", "service", "reason"]))
    lines.extend([
        "",
        "개인정보/릴리스 정책: `.env`, `.runtime/`, 모델 캐시, 로그, build output, timestamp runtime validation report는 릴리스 패키지에서 제외한다.",
        "",
        "## 운영 해석",
        "",
        "이 리포트는 어떤 파일이 source artifact이고 어떤 파일이 로컬 runtime state인지 구분하기 위한 문서다. `.runtime/`은 compose 실행에 필요한 로컬 secret state이므로 작업 트리에 생길 수 있지만, release package에는 들어가면 안 된다. 모델 cache와 logs도 재생성 가능한 환경 산출물이므로 배포 ZIP에 포함하지 않는다.",
        "",
        "정리 작업 전에는 `make cleanup-plan`으로 삭제 대상을 먼저 확인하고, 모델 cache나 runtime secret처럼 비용이 큰 항목은 명시적인 purge flag를 사용할 때만 삭제한다.",
    ])
    return "\n".join(lines) + "\n"


def write_storage_paths_report(registry: StorageRegistry, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    document = registry.as_report_document()
    json_path = output_dir / "storage_paths.json"
    md_path = output_dir / "storage_paths.md"
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_storage_report_markdown(document), encoding="utf-8")
    return json_path, md_path
