# 최종 점검 체크리스트

릴리스 전 다음 항목을 확인한다.

- [ ] `make validate` (계약 검증, runtime validation config-only, vLLM compose 검증을 모두 포함한다)
- [ ] `make test`
- [ ] `python -m compileall -q src scripts tests`
- [ ] `make package`
- [ ] release zip에 `.env`, logs, runtime secret, model cache가 없는지 확인
- [ ] Gateway `/docs`, `/redoc`, `/openapi.json` 접근 확인
- [ ] Risk Adapter `/docs`, `/openapi.json` 접근 확인
- [ ] compose 환경에서 `/ready`가 admin auth 정합성을 만족하는지 확인
- [ ] Prometheus `9410`, Grafana `9411`, DCGM `9412` 노출 확인
- [ ] GPU host에서 full runtime validation 수행
