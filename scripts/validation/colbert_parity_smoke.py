#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.retrieval import ColbertReferenceAdapter


DEFAULT_QUERY = "대한민국의 수도는 어디인가?"
DEFAULT_DOCUMENTS = [
    "파리는 프랑스의 수도이다.",
    "서울은 대한민국의 수도이며 정치와 경제의 중심지이다.",
    "부산은 대한민국의 항구 도시이다.",
]


def _scores_from_vllm(base_url: str, query: str, documents: list[str], timeout: float) -> list[float]:
    response = httpx.post(
        f"{base_url.rstrip('/')}/score",
        json={
            "model": "local-colbert-ko",
            "encoding_format": "float",
            "text_1": query,
            "text_2": documents,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    scores = {int(item["index"]): float(item["score"]) for item in payload.get("data", [])}
    return [scores[index] for index in range(len(documents))]


def _token_embeddings_shape(base_url: str, text: str, timeout: float) -> tuple[int, int]:
    response = httpx.post(
        f"{base_url.rstrip('/')}/pooling",
        json={"model": "local-colbert-ko", "input": [text], "task": "token_embed"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", [])
    if not data:
        raise RuntimeError("vLLM /pooling returned no data")
    embedding = data[0].get("data", data[0].get("embedding", []))
    if not embedding or not isinstance(embedding, list) or not isinstance(embedding[0], list):
        raise RuntimeError("vLLM /pooling did not return a token embedding matrix")
    return len(embedding), len(embedding[0])


def _rank(scores: list[float]) -> list[int]:
    return [index for index, _ in sorted(enumerate(scores), key=lambda item: item[1], reverse=True)]


def _load_artifact_manifest(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    manifest_path = path / "artifact_manifest.json"
    config_path = path / "config.json"
    if not manifest_path.exists() or not config_path.exists():
        raise RuntimeError(f"missing ColBERT artifact manifest/config under {path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if manifest.get("projection_required") is not True:
        raise RuntimeError("artifact_manifest.json must declare projection_required=true")
    if config.get("projection_dim") != 128 or config.get("projection_filename") != "proj.pt":
        raise RuntimeError("ColBERT artifact config must declare proj.pt projection_dim=128")
    return {"manifest": manifest, "config": config}


def _observed_forward_paths(log_file: Path | None) -> list[dict[str, Any]]:
    if log_file is None:
        return []
    if not log_file.exists():
        raise RuntimeError(f"forward shape log file does not exist: {log_file}")
    observations: list[dict[str, Any]] = []
    pattern = re.compile(r"ColBERT-ko forward shape trace: (\{.*\})")
    for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if not match:
            continue
        payload = ast.literal_eval(match.group(1))
        if payload.get("event") == "colbert_ko_forward_shape_trace":
            observations.append(payload)
    return observations


def run(args: argparse.Namespace) -> dict[str, Any]:
    if len(args.documents) < 2:
        raise RuntimeError("ColBERT parity smoke requires at least two documents for /score.")
    artifact = _load_artifact_manifest(args.artifact_dir)
    reference = ColbertReferenceAdapter()
    projection_dim = int(getattr(reference.projection, "out_features", 0))
    if projection_dim != 128:
        raise RuntimeError(f"reference proj.pt must produce 128 dims, got {projection_dim}")

    ref_scores = reference.score(args.query, args.documents)
    vllm_scores = _scores_from_vllm(args.vllm_base_url, args.query, args.documents, args.timeout)
    ref_rank = _rank(ref_scores)
    vllm_rank = _rank(vllm_scores)
    token_count, token_dim = _token_embeddings_shape(args.vllm_base_url, args.documents[ref_rank[0]], args.timeout)

    if token_dim != 128:
        raise RuntimeError(f"vLLM token embeddings must be 128-d after proj.pt, got {token_dim}")
    if ref_rank[0] != vllm_rank[0]:
        raise RuntimeError(f"top-1 mismatch: reference={ref_rank[0]} vllm={vllm_rank[0]}")
    if args.require_full_order and ref_rank != vllm_rank:
        raise RuntimeError(f"ranking order mismatch: reference={ref_rank} vllm={vllm_rank}")
    observed_forward_paths = _observed_forward_paths(args.forward_shape_log)
    if args.require_forward_trace and not observed_forward_paths:
        raise RuntimeError(
            "no ColBERT-ko forward shape trace found. Start colbert-ko-vllm with "
            "COLBERT_KO_TRACE_FORWARD_SHAPES=1 and pass --forward-shape-log."
        )

    return {
        "status": "pass",
        "model": "local-colbert-ko",
        "trace_forward_shapes_env": "COLBERT_KO_TRACE_FORWARD_SHAPES=1",
        "query": args.query,
        "documents": args.documents,
        "reference_scores": ref_scores,
        "vllm_scores": vllm_scores,
        "reference_rank": ref_rank,
        "vllm_rank": vllm_rank,
        "top1_index": ref_rank[0],
        "projection_dim": projection_dim,
        "vllm_token_embedding_shape": [token_count, token_dim],
        "observed_forward_paths": observed_forward_paths,
        "artifact": artifact,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GPU/live ColBERT-ko reference-vs-vLLM plugin runtime ranking parity smoke."
    )
    parser.add_argument("--vllm-base-url", default=os.getenv("COLBERT_KO_VLLM_BASE_URL", "http://localhost:9404"))
    parser.add_argument("--artifact-dir", type=Path, default=Path(os.getenv("COLBERT_KO_MODEL_DIR", "models/colbert-ko-vllm")))
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--document", dest="documents", action="append", help="Repeat to override the default fixture documents.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--require-full-order", action="store_true")
    parser.add_argument(
        "--forward-shape-log",
        type=Path,
        default=Path(os.environ["COLBERT_KO_FORWARD_SHAPE_LOG"])
        if os.getenv("COLBERT_KO_FORWARD_SHAPE_LOG")
        else None,
        help=(
            "Optional colbert-ko-vllm log file captured while "
            "COLBERT_KO_TRACE_FORWARD_SHAPES=1 is set; parsed into observed_forward_paths."
        ),
    )
    parser.add_argument("--require-forward-trace", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.documents:
        args.documents = list(DEFAULT_DOCUMENTS)
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
