# 런타임 리포트 디렉터리

`python scripts/validation/runtime_validation.py`를 실행하면 이 디렉터리에 timestamp가 붙은 JSON/Markdown 리포트가 생성된다. 이 리포트는 host, GPU, vLLM image, token, 포트 상태의 영향을 받는 실행 산출물이므로 release ZIP에는 기본 포함하지 않는다.

Timestamp가 붙은 과거 runtime validation 리포트는 당시 설정의 실행 증거이며, 새로운 runtime target으로 소급 수정하지 않는다. 현재 source-of-truth projection은 `runtime_targets.*`, `operator_status_bundle.*`, `monitoring_projection.*`, `live_evidence_bundle.*`와 최신 timestamp 리포트를 기준으로 확인한다.

## 사용 방법

```bash
make compose-up
python scripts/validation/runtime_validation.py
```

운영 인수인계 시에는 최신 리포트의 PASS/FAIL뿐 아니라 실패한 endpoint, Prometheus target 상태, Grafana rendering 여부를 함께 기록한다.
