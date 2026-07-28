# 로컬 저장소 경로

이 문서는 프로젝트가 로컬에 생성하거나 mount하는 저장 경로를 한 곳에서 추적하기 위한 운영 가이드다. 기준 원천은 `configs/storage_paths.yaml`이다.

## 빠른 확인

```bash
make storage-paths
```

생성 파일:

```text
reports/runtime/storage_paths.json
reports/runtime/storage_paths.md
```

## 핵심 원칙

- `.env`, `.runtime/`, model cache, logs, build output, timestamped runtime validation report는 release package에 포함하지 않는다.
- 모델 다운로드 캐시는 compose 파일과 함께 관리하기 쉽도록 기본적으로 `ops/compose/model_cache/huggingface`에 모은다. `.env`에는 `HF_CACHE_DIR=./model_cache/huggingface`로 표시되며, Docker Compose는 이 상대 경로를 `ops/compose/*.yaml` 기준으로 해석한다.
- full-stack compose는 `HF_CACHE_DIR`을 vLLM 컨테이너 내부 `/root/.cache/huggingface`로 mount한다.
- `.runtime/`과 `model_cache/`는 실수 삭제 비용이 크므로 기본 cleanup에서 보존한다.
- 파괴적 cleanup은 `PURGE_MODEL_CACHE=1`, `PURGE_RUNTIME_SECRETS=1`, `PURGE_VENV=1`처럼 명시적 flag가 필요하다.

## 기본 경로

| 경로 | 용도 | 기본 cleanup |
|---|---|---|
| `.env` | 로컬/compose runtime 설정과 secret | 삭제하지 않음 |
| `.runtime/` | Prometheus bearer token 등 runtime secret file | 기본 보존, `PURGE_RUNTIME_SECRETS=1`일 때만 삭제 |
| `reports/runtime/` | runtime/operator/status/evidence report | host별 생성 산출물이며 release package에서 제외 |
| `logs/` | local app-only log | `make clean-all`에서 삭제 |
| `run/` | local process pid tracking | `make clean`에서 삭제 |
| `dist/` | release ZIP | `make clean`에서 삭제 |
| `build/` | build artifact | `make clean`에서 삭제 |
| `.venv/` | bootstrap virtualenv | bootstrap 재생성, reset에서 `PURGE_VENV=1`일 때 삭제 |
| `ops/compose/model_cache/huggingface/` | Hugging Face/vLLM model cache 기본 위치 | 기본 보존, `PURGE_MODEL_CACHE=1`일 때 삭제 |
| `model_cache/huggingface/` | 선택적 repository-root Hugging Face cache override | 기본 보존, `PURGE_MODEL_CACHE=1`일 때 삭제 |
| `models/` | 선택적 local model artifact | 기본 보존, `PURGE_MODEL_CACHE=1`일 때 삭제 |

## full-stack compose 모델 캐시

`.env.compose.example`은 다음 기본값을 제공한다. 이 상대 경로는 Docker Compose가 compose 파일 위치 기준으로 해석하므로 실제 host path는 `ops/compose/model_cache/huggingface`가 된다.

```text
HF_CACHE_DIR=./model_cache/huggingface
```

`ops/compose/full-stack.private-network.yaml`의 모든 vLLM service는 이 host path를 다음 container path로 mount한다.

```text
/root/.cache/huggingface
```

이렇게 하면 vLLM 컨테이너 재생성 후에도 모델 다운로드 캐시를 유지할 수 있다.

## 삭제 전 확인

```bash
make remove-plan
```

모델 캐시까지 지울 때만 다음을 사용한다.

```bash
PURGE_MODEL_CACHE=1 make clean-all
```

runtime secret까지 재생성해야 하는 경우에만 다음을 사용한다.

```bash
PURGE_RUNTIME_SECRETS=1 make clean-all
make sync-runtime-secrets
```
