from ai_model_serving.apps.mlx_metrics_exporter import render_mlx_metrics


def test_mlx_json_metrics_projection_excludes_recent_payload_and_maps_runtime_signals() -> None:
    body = render_mlx_metrics(
        {
            "summary": {"requests_completed": 3, "in_flight": 1, "prompt_tokens_total": 120},
            "server": {"loaded_model": "/cache/model", "request_queue_depth": 2, "effective_context_limit": 32768},
            "latest": {
                "prefill_tok_s": 91.5,
                "decode_tok_s": 12.25,
                "peak_memory_gb": 16.5,
                # 응답 timings에는 있지만 mlx-vlm /metrics 계약에는 없는 값이다.
                # exporter가 비계약 필드를 운영 지표처럼 투영하지 않는지도 확인한다.
                "draft_n_accepted": 6,
                "draft_n": 8,
                "prompt": "must never become a metric",
            },
            "recent": [{"generated_text": "must never become a metric"}],
        },
        scrape_success=True,
    )

    assert "mlx_runtime_scrape_success 1" in body
    assert "mlx_runtime_requests_completed_total 3" in body
    assert "mlx_runtime_effective_context_limit_tokens 32768" in body
    assert "mlx_runtime_latest_peak_memory_gib 16.5" in body
    assert "speculative_acceptance" not in body
    assert "must never become a metric" not in body
