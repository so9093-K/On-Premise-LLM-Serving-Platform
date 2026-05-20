from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs/adr"
EXAMPLES_DOC = ROOT / "docs/examples/requests.md"


def read_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def load_yaml(rel: str) -> dict[str, Any]:
    return yaml.safe_load(read_text(rel))


def load_json(rel: str) -> dict[str, Any]:
    return json.loads(read_text(rel))


def load_manifest() -> dict[str, Any]:
    return load_yaml("docs/manifest.yaml")


def stable_report_markdown_files() -> set[str]:
    return {
        str(path.relative_to(ROOT))
        for path in (ROOT / "reports").rglob("*.md")
        if not re.search(r"runtime_validation_\d{8}T\d{6}Z\.md$", path.name)
    }


def public_model_ids() -> set[str]:
    catalog = load_yaml("configs/model_catalog.yaml")
    return {
        model_id
        for model_id, model in catalog["models"].items()
        if model.get("gateway_listing", {}).get("enabled") is True
        and model.get("lifecycle", {}).get("exposure") == "public"
    }


def runtime_service_ports() -> dict[str, int]:
    serving = load_yaml("configs/model_serving.yaml")
    return {
        model["endpoint"].split("//", 1)[1].split("/v1", 1)[0]: int(model["port"])
        for model in serving["models"].values()
        if model.get("backend") == "vllm"
    }
