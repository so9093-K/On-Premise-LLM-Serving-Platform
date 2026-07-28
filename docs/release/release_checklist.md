# 릴리스 체크리스트

릴리스는 서비스를 기동하는 작업이 아니라, 정적 계약·문서·패키지 위생·운영 projection이 안전한지 확인한 뒤 ZIP을 만드는 작업이다. live runtime 검증은 target Docker/GPU 서버에서 별도로 수행한다.

## 정적 계약 gate

```bash
make validate
python scripts/validation/validate_contracts.py
python scripts/validation/runtime_validation.py --config-only
python scripts/compose/validate_vllm_compose.py
```

검증 대상은 OpenAPI refs, generated OpenAPI schema injection, JSON Schema, YAML, 포트, forbidden field invariant, compose/model registry 정합성이다.

## 운영 projection gate

```bash
make operator-reports
make model-validate
make model-diff
make auth-doctor
```

operator report는 runtime target, storage path, monitoring projection, operator status, live evidence placeholder를 갱신한다. `model-diff`는 registry projection과 checked-in artifact drift를 확인한다.

## Live runtime gate

Docker/GPU/vLLM이 있는 target host에서 실행한다.

```bash
make rebuild-vllm-unified
make risk-vllm-config-check
make compose-up
make ready-full
make runtime-validate
make operator-reports
```

Risk vLLM patch verify는 production 승격에서 skip하지 않는다.

## 패키지 gate

```bash
make validate
make test
make package
```

ZIP에는 `.env`, `.runtime`, cache, logs, model cache, timestamp runtime validation report, pycache, egg-info가 포함되면 안 된다. ZIP root는 `ai_model_serving_platform/`로 고정한다.

Archive 정책: `docs/archive/`는 historical documentation으로 release package에 포함할 수 있다. `reports/archive/`는 handoff evidence 보존용이며 package script가 제외하지 않는 한 포함 가능하지만, active 운영 guide나 generated runtime evidence로 취급하지 않는다. 포함 여부를 바꾸려면 `scripts/build/package_release.sh`의 제외 규칙과 `docs/manifest.yaml` lifecycle을 함께 갱신한다.

## 인증 제어 플레인 점검

```bash
make auth-status
make auth-doctor
make auth-plan MODE=strict
```

`local_open`은 외부 접근이 차단된 신뢰된 사내망용이다. 이 profile은
`master_open/private_lan`과 함께 사용하며, internet-reachable release에는
사용하지 않는다. 그 외 release에서는 public API, admin endpoint, internal
service auth가 의도한 profile과 일치해야 한다.

## Generated OpenAPI 계약 gate

FastAPI generated OpenAPI는 checked-in schema injection을 통해 수동 specs보다 느슨해지지 않아야 한다. request/response body, security state, docs description drift를 수정할 때는 `validate_contracts.py`와 관련 테스트를 함께 갱신한다.

## 계약 검증용 marker

아래 명령 원문은 release/governance validation exact-match와 호환하기 위해 보존한다.

- make runtime-targets
- make monitoring-projection
- make operator-status
- make runtime-validate
- make package
