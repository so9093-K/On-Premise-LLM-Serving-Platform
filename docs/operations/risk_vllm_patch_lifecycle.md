# Risk vLLM patch lifecycle 관리

Risk detector image는 main Gemma4 vLLM image와 분리되어 있다. Kanana Prompt detector는 explicit Llama `head_dim` config를 사용하며, 일부 Transformers/vLLM 조합은 runtime 시작 전 config validation 단계에서 이를 거부할 수 있다. 현재 patch는 이 호환성 문제를 넘기기 위한 **임시 compatibility bridge**이며, 영구 fork 전략이 아니다.

## 결론

- 단기 운영에서는 현재 patch를 사용할 수 있다.
- 장기적으로 vendor/site-packages patch는 위험하다.
- patch 상태는 image label, metadata JSON, verify script, release gate로 계속 증명해야 한다.
- upstream Transformers/vLLM 조합이 explicit `head_dim`을 정상 처리하면 patch를 제거해야 한다.

## 원천 파일

| 파일 | 역할 |
|---|---|
| `ops/docker/Dockerfile.risk-vllm-kanana` | dedicated risk vLLM image를 만들고 patch OCI label을 선언한다. |
| `ops/patches/transformers_llama_head_dim_guard.py` | `head_dim` validation guard patch를 적용하고 검증한다. |
| `ops/patches/README.md` | patch 이유, lifecycle, 제거 조건을 설명한다. |
| `scripts/build/build_risk_vllm_image.sh` | Docker build에 compatibility floor 값을 전달한다. |
| `scripts/models/check_risk_vllm_image_config.sh` | image label, patch metadata, Kanana HF config load를 image 내부에서 확인한다. |

## 운영자 흐름

```bash
make rebuild-risk-vllm
make risk-vllm-config-check
make risk-vllm-patch-removal-check
```

`make first-run`/`make bootstrap`은 full local/full-stack setup 중 risk vLLM image build와 config check를 함께 실행한다. `make preflight-compose`도 runtime local check가 아닌 경우 image config check를 수행한다. `make risk-vllm-patch-removal-check`는 현재 image 내부 Transformers 파일 모양을 보고 patch 제거 후보 상태를 알려주지만, 이미 patch가 적용된 image만으로 제거 가능성을 증명하지는 않는다.

## 메타데이터 계약

Risk image에는 다음 증빙이 있어야 한다.

- OCI label `ai_model_serving.patch.transformers_llama_head_dim_guard=true`
- OCI label `ai_model_serving.patch.reason=Kanana explicit head_dim compatibility`
- metadata JSON `/usr/local/share/ai-model-serving/patches/transformers_llama_head_dim_guard.json`

metadata는 package version, target file path, original/patched file hash, 제거 조건을 담는다. 이 값은 장애 분석과 릴리스 리뷰용이며 runtime payload 데이터는 포함하지 않는다.

## 사이드이펙트 정책

이 patch는 `RISK_VLLM_IMAGE`에만 적용한다. 다음은 바꾸면 안 된다.

- main Gemma4 runtime image
- API path
- request/response schema
- model id
- compose service topology
- risk signal-only semantics

## 제거 조건

다음이 모두 만족되면 patch를 제거한다.

1. 선택한 Transformers/vLLM 조합이 image-level patch 없이 Kanana explicit `head_dim` config를 정상 load한다.
2. `make risk-vllm-config-check`가 `risk-prompt`, `risk-siren` 모두에서 통과한다.
3. release report에 patch 제거 전/후 image digest와 config load 결과가 남는다.
4. `make risk-vllm-patch-removal-check`가 제거 후보 상태를 설명하고, patch 없는 candidate image에서 canary가 통과한다.
5. 운영 문서와 Docker label/metadata 검증이 patch 미사용 상태를 반영한다.

## 장기 리스크 판단

vendor/site-packages patch가 위험한 이유는 다음과 같다.

| 리스크 | 설명 | 완화책 |
|---|---|---|
| Upgrade fragility | Transformers 내부 validation 코드가 이동하거나 바뀌면 text patch가 깨질 수 있다. | version floor와 image digest를 기록하고, upgrade마다 config check 수행 |
| Supply-chain ambiguity | 설치된 package metadata와 실제 실행 코드가 달라진다. | metadata hash와 Docker label을 release evidence에 포함 |
| Debug complexity | upstream bug와 local image patch 문제를 구분하기 어렵다. | patch verify, canary config load, failure triage 문서 유지 |
| Removal failure | 필요 없어졌는데 patch가 계속 남을 수 있다. | release checklist에 removal check 추가 |

production 승격 전에는 `SKIP_RISK_VLLM_PATCH_VERIFY=1` 같은 우회 옵션을 사용하지 않아야 한다.

## 계약 검증용 marker

아래 원문은 기존 governance validation exact-match와 호환하기 위해 보존한다.

- Patch lifecycle
- metadata
- removal condition
