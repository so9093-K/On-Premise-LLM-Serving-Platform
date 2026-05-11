# 전체 파일 위생·빌드·삭제 디버깅 감사 — Current

이 문서는 프로젝트를 처음 받은 상태처럼 다시 풀어 전체 파일을 검토한 current audit 기록이다. 목적은 active source, 문서, 테스트, 생성 산출물, 삭제/빌드 UX의 경계를 명확히 하여 릴리스 패키지에 불필요한 파일이 다시 섞이지 않게 하는 것이다.

## 검토 범위

- root metadata: `README.md`, `Makefile`, `Dockerfile`, `pyproject.toml`, lockfile, env example
- application source: `src/ai_model_serving/**`
- operator/developer commands: `scripts/**`
- configuration and contracts: `configs/**`, `contracts/**`, `specs/**`, `harness/**`
- runtime/ops templates: `ops/**`, `model_cards/**`, `examples/**`, `adr/**`
- tests: `tests/unit/**`, `tests/contract/**`
- documentation and reports: `docs/**`, `reports/refactor/**`, `reports/runtime/**`
- generated/local artifacts: `dist/`, `.runtime/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `build/`, `outputs/`, `run/`, `logs/`

## 삭제·제외 대상 식별 결과

다음 파일/디렉터리는 source of truth가 아니라 로컬 실행·테스트·패키징 중 생기는 산출물이다. active source review에서는 삭제 대상이며, 릴리스 패키지에는 포함하지 않는다.

| 대상 | 상태 | 조치 |
|---|---|---|
| `__pycache__/` | Python import/test가 생성 | `make clean`으로 삭제 |
| `*.pyc` | Python bytecode cache | `make clean`으로 삭제 |
| `.pytest_cache/` | pytest 로컬 캐시 | `make clean`으로 삭제 |
| `dist/` | 패키징 산출물 | source tree review 후 재생성, 패키지 내부에는 미포함 |
| `.runtime/` | 로컬 runtime secret/token | `PURGE_RUNTIME_SECRETS=1 make clean-all`일 때만 삭제 |
| `build/`, `outputs/`, `run/` | 로컬 빌드/실행 산출물 | `make clean`으로 삭제 |
| `logs/` | 로컬 로그 | `make clean-all`로 삭제 |
| `model_cache/`, top-level `models/` | 대형 모델 캐시 | 기본 보존, `PURGE_MODEL_CACHE=1`일 때만 삭제 |

이번 감사에서 소스 코드·문서·테스트 중 즉시 삭제할 활성 파일은 발견하지 않았다. `docs/refactor/phase*.md`는 오래된 운영 지침이 아니라 phase history로 분류되어 `docs/refactor/README.md`에서 현재 문서와 분리되어 있다. `src/ai_model_serving/validation.py` 같은 compatibility facade도 legacy debris가 아니라 하위 호환 entrypoint로 유지한다.

## 빌드·패키징 디버깅 결과

패키징은 `make package` → `scripts/build/package_release.sh` 경로를 사용해야 한다. 이 경로는 staging directory를 만들고 다음 항목을 제외한 뒤 ZIP을 만든다.

- `.env`, `.env.*` 단, safe examples는 포함
- `.runtime/`
- `dist/`, `build/`, `logs/`, `outputs/`, `run/`
- top-level `model_cache/`, `models/`
- `__pycache__/`, `*.pyc`, `*.pyo`, `*.egg-info/`
- timestamped live runtime validation reports

주의: 프로젝트 root를 그대로 수동 ZIP으로 묶으면 로컬 `dist/`, `.runtime/`, bytecode cache가 섞일 수 있다. 릴리스/전달용 파일은 반드시 `make package` 또는 동일한 exclusion policy를 사용해야 한다.

## 삭제 과정 디버깅 결과

`make clean-dry-run`은 실제 삭제 없이 삭제 후보를 모두 출력한다. 기본 clean은 source와 docs를 삭제하지 않고 generated artifacts만 제거한다.

```bash
make clean-dry-run
make clean
make clean-all
PURGE_RUNTIME_SECRETS=1 make clean-all
PURGE_MODEL_CACHE=1 make clean-all
```

삭제 정책은 다음과 같다.

- 실행 중인 local service pid가 있으면 기본 clean을 거부한다.
- `.runtime/`은 기본 삭제하지 않는다. runtime secret 재발급을 의도할 때만 `PURGE_RUNTIME_SECRETS=1`을 사용한다.
- `model_cache/`, top-level `models/`는 기본 삭제하지 않는다. 대형 캐시 삭제를 의도할 때만 `PURGE_MODEL_CACHE=1`을 사용한다.
- `docs/build/`, `docs/models/`는 문서 source이므로 top-level `build/`, `models/` exclusion과 구분해 보존한다.

## 테스트·검증 디버깅 결과

정적 검증과 deterministic test가 통과해야 패키징 완료 상태로 본다.

```bash
python -m compileall -q src scripts tests
PYTHONPATH=src python scripts/validation/validate_contracts.py
PYTHONPATH=src python scripts/validation/release_check.py --step-timeout-seconds 60
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
```

현재 기준 expected result:

- contract validation: PASS
- static release check: PASS
- deterministic pytest: `198 passed`

## 남은 운영 확인

코드/문서/테스트 레벨에서 불필요한 생성 산출물은 제거 가능 상태이며, 최신 전체 흐름 재검토는 `current_first_run_clean_package_audit.md`를 기준으로 한다. 남은 확인은 실제 운영 환경에서 수행해야 한다.

1. `make clean-dry-run` 결과가 운영자가 예상한 삭제 범위와 일치하는지 확인
2. 실제 배포 전 `make package`로 만든 ZIP만 전달하는지 확인
3. 수동 ZIP이나 파일 복사 경로에서 `.runtime/`, `dist/`, `__pycache__/`가 섞이지 않는지 확인
4. streaming runtime은 실제 vLLM + proxy 환경에서 `curl -N` smoke로 별도 확인
