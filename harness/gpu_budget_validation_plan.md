# GPU 예산 검증 계획

이 프로젝트는 설정 파일에서 GPU 사용량과 concurrency를 보수적으로 제한하지만, 실제 안전성은 target GPU host에서 검증해야 한다. 이 문서는 48GB급 GPU 기준 검증 절차를 정리한다.

## 검증 항목

1. enabled vLLM process가 동시에 올라오는지 확인한다.
2. 각 모델의 cold start 시간과 peak VRAM을 기록한다.
3. main LLM, embedding, risk prompt, risk siren 요청을 순차 실행한다.
4. queue timeout과 circuit breaker가 의도대로 동작하는지 확인한다.
5. DCGM exporter, Prometheus, Grafana가 같은 시점의 metric을 보여주는지 확인한다.

## 실패 시 조치

- OOM이면 `configs/model_serving.yaml`의 `gpu_memory_utilization`, `max_model_len`, `max_concurrency`를 낮춘다.
- latency가 높으면 concurrency를 올리기 전에 queue timeout과 VRAM headroom을 먼저 본다.
- monitoring scrape가 실패하면 `.runtime/prometheus/admin_api_key`와 `.env`의 `ADMIN_API_KEY`를 비교하고 `make sync-runtime-secrets`를 실행한다.
