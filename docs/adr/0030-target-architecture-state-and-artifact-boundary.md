# ADR-0030: Target architecture, state semantics와 artifact boundary

- Status: Accepted
- Date: 2026-09-18
- Supersedes: [ADR-0029](./0029-canonical-terminology.md) 중 stable identifier와 상태 의미에 관한 장기 결정

## Context

프로젝트는 초기 단일 GPU serving stack에서 Gateway, Runtime Controller, Main Model switching,
Configuration Plane, deployment target, qualification과 self-hosted Control Plane까지 확장됐다.
그 과정에서 초기 구현 이름과 하나의 상태 필드가 여러 의미를 동시에 맡게 됐다.

대표적인 문제는 다음과 같다.

- `MAIN_LLM_*`, `admin-sidecar`, `risk_adapter`, `risk_prompt`가 현재 역할보다 좁거나
  구현 형태를 이름에 노출한다.
- Main Model의 `compatibility.status`가 기술 호환성과 실제 qualification 수준을 동시에 표현한다.
- Deployment Target의 `validation_status`가 구현 여부와 qualification 여부를 동시에 표현한다.
- Main Model checkpoint identity가 model catalog, serving policy, profile 사이에 중복될 수 있다.
- image build, registry publish, promotion, deployment를 미래 자동화에서 한 단계로 묶을 위험이 있다.

ADR-0029는 사용자-facing terminology와 기존 식별자를 분리해 즉시 rename으로 인한 호환성 파손을
막았다. 이 결정은 migration 안전성에는 유효하지만, 기존 식별자를 영구 canonical로 간주할 근거는
되지 않는다. 장기 target state는 migration 비용이 아니라 의미와 책임 경계를 먼저 기준으로 정한다.

## Decision

### 1. Target state를 migration cost보다 먼저 결정한다

식별자나 상태 모델을 변경할 때 다음 순서를 따른다.

1. 의미와 책임 경계를 기준으로 target state를 정한다.
2. 외부 계약과 내부 식별자를 구분한다.
3. 현재 사용 범위와 migration 비용을 계산한다.
4. compatibility alias와 migration 기간을 설계한다.
5. canonical 생성물과 문서를 새 target으로 수렴시킨다.
6. compatibility 기간 뒤 legacy 식별자를 제거한다.

사용 범위가 크다는 사실은 migration 순서와 기간을 결정하지만 잘못된 이름이나 상태 모델을
영구 canonical로 만드는 근거로 사용하지 않는다.

### 2. Identifier의 장기 canonical target을 역할 중심으로 둔다

장기 target은 다음과 같다.

| 현재 식별자 | 장기 canonical target | 비고 |
|---|---|---|
| `MAIN_LLM_*` | `MAIN_MODEL_*` | operator-facing namespace는 canonical 전환 완료; legacy read/sync alias 유지 |
| `MAIN_LLM_MODEL` | `MAIN_MODEL_ALIAS` | 실제 의미는 checkpoint가 아니라 Public Model Alias |
| `admin-sidecar` / `admin_sidecar` | Runtime Controller 계열 식별자 | service/module rename은 별도 migration |
| `risk_adapter` / `risk-adapter` | Risk Signal Service 계열 식별자 | 공개 `/v1/risk/*` path는 변경 대상이 아님 |
| `risk_prompt` / `risk-prompt` | Prompt Injection Detector 계열 식별자 | 공개 risk detector contract와 분리 |

기존 식별자는 migration 전까지 compatibility identifier다. 단순 검색/치환으로 제거하지 않는다.

### 3. Main Model의 compatibility와 qualification을 분리한다

장기 canonical state는 두 축이다.

```yaml
compatibility:
  status: compatible | incompatible | unknown
qualification:
  status: verified | unverified
```

- **Compatibility**는 현재 deployment/runtime 조합이 기술적으로 가능한지를 나타낸다.
- **Qualification**은 해당 조합에 대해 요구된 실제 검증 근거가 확보됐는지를 나타낸다.
- `likely`는 canonical 상태가 아니며 legacy provisional 상태로만 migration한다.
- switch 가능 여부는 compatibility가 결정하고, 추가 확인 필요 여부는 qualification이 결정한다.

기존 Admin API의 `compatibility.status` 값을 즉시 깨지 않는다. 코드 migration에서는 새 canonical
축을 내부/config에 먼저 도입하고, 기존 API 필드는 compatibility 기간 동안 legacy projection으로
유지한다. 새 canonical 상태를 읽을 수 있는 명시적 projection을 추가한 뒤 제거 시점을 별도로 결정한다.

### 4. Deployment Target의 구현 상태와 qualification을 분리한다

장기 canonical state는 다음 두 축이다.

```yaml
implementation_status: planned | implemented
qualification_status: verified | unverified
```

`verified / implemented / planned / unvalidated`를 한 enum에 섞지 않는다.

- 실행 가능한지 여부는 `implementation_status`가 소유한다.
- 실제 검증 근거 수준은 `qualification_status`가 소유한다.
- 기존 `validation_status` projection은 compatibility 기간 동안 유지할 수 있다.

### 5. Main Model checkpoint identity는 Main Model Profile이 소유한다

Main Model의 실제 실행 identity는 profile이 단일 Source of Truth가 된다.

- `main_model_profiles.yaml`: model ID, revision, runtime image, command, modality, request policy, qualification
- `model_catalog.yaml`: Public Model Alias, logical discovery/capability
- `model_serving.yaml`: 공통 connectivity, timeout, admission과 serving behavior

`local-main` 같은 Public Model Alias와 실제 upstream checkpoint identity를 분리한다.
현재 중복 필드는 별도 migration PR에서 제거하며 이 ADR만으로 runtime behavior를 바꾸지 않는다.

### 6. Qualification은 상태만이 아니라 evidence로 확장 가능해야 한다

`verified`는 장기적으로 최소 다음 정보를 추적할 수 있어야 한다.

- model ID와 immutable revision
- runtime engine/version
- runtime image digest
- deployment target와 hardware/driver context
- 검증한 capability와 결과
- 검증 시각

evidence의 저장 형식과 retention은 별도 변경에서 정의한다. 상태 모델은 이 확장을 막지 않아야 한다.

### 7. Git은 source authority이며 build artifact 저장소가 아니다

Git repository는 source, configuration, lock file, Dockerfile, build/deploy contract를 소유한다.
Docker/OCI image 자체를 Git history에 저장하지 않는다.

로컬 개발은 registry 없이 동작해야 한다.

```text
source checkout
  → repository build script
  → local Docker image
  → local runtime
```

원격 재사용·promotion·rollback 요구가 생기면 OCI-compatible registry를 distribution adapter로
추가할 수 있다. registry provider는 core application contract가 아니다.

```text
source
  → build
  → optional publish
  → immutable name@sha256 digest
  → optional promote
  → deploy
```

GitHub Container Registry, Harbor, 사내 OCI registry 등 특정 provider 이름이나 credential을
core config의 Source of Truth로 만들지 않는다.

### 8. Build, publish, promote, deploy를 서로 다른 책임으로 유지한다

향후 자동화에서도 다음 단계를 하나의 암묵적 workflow로 합치지 않는다.

- **Build**: repository-owned script가 source와 locked input으로 image를 만든다.
- **Publish**: 선택한 artifact store에 image를 전송하고 immutable digest를 얻는다.
- **Promote**: qualification을 통과한 digest를 다음 environment의 후보로 선택한다.
- **Deploy**: 명시적으로 전달된 immutable artifact를 대상 host에 적용한다.

Platform Image와 Unified vLLM Image는 서로 다른 lifecycle을 유지한다. 일반 application 변경이
대형 CUDA/vLLM artifact rebuild를 암묵적으로 유발하면 안 된다.

### 9. Image CI/publish는 현재 필수 gate로 도입하지 않는다

현재 GitHub Actions의 application/contract gate에 Docker build, registry publish, Unified vLLM
build를 즉시 추가하지 않는다. 자동화 도입 전 다음이 먼저 확인돼야 한다.

- 막으려는 구체적 운영 손실
- artifact input graph와 invalidation 기준
- cold/warm build 시간과 cache 비용
- runner architecture와 trust boundary
- cache read/write 권한
- verification image와 release candidate의 구분
- publish/retention/rollback 정책

필요성이 확인되면 manual/non-blocking pilot에서 시작하고 측정 결과를 근거로 required gate 여부를
결정한다.

### 10. Offline/on-prem transport를 registry와 독립적으로 유지한다

소규모 또는 완전 격리 환경은 `docker save/load` 같은 file transport를 사용할 수 있다.
내부 registry가 필요한 규모가 되더라도 deployment contract는 특정 registry가 아니라 immutable
OCI reference와 explicit artifact input을 기준으로 유지한다.

SBOM, provenance, signature는 향후 OCI artifact metadata로 추가할 수 있지만 현재 도입의 선행 조건이 아니다.

## Migration order

이 ADR 이후의 구현 순서는 다음을 기본으로 한다.

1. Main Model compatibility / qualification 분리와 legacy projection
2. Deployment Target implementation / qualification 분리와 legacy projection
3. Main Model checkpoint Source of Truth 수렴
4. Qualification evidence v1
5. official OpenAI SDK conformance
6. `MAIN_LLM_* → MAIN_MODEL_*` operator namespace migration
7. Runtime Controller authority/threat model — [ADR-0031](./0031-runtime-controller-docker-authority-boundary.md)
8. Runtime Controller / Risk Signal Service / Prompt Injection Detector 내부 identifier migration
9. artifact automation은 실제 운영 trigger가 생긴 경우 별도 ADR/PR로 진행

각 단계는 독립 rollback이 가능하도록 별도 PR로 나눈다.

## Consequences

- 기존 식별자를 즉시 제거하지 않으면서도 장기 canonical target이 명확해진다.
- compatibility와 qualification이 다른 질문이라는 사실이 code/config/API migration의 기준이 된다.
- Main Model identity 중복을 제거할 책임 위치가 명확해진다.
- registry가 없는 로컬 개발과 registry 기반의 다중-host 운영을 같은 build/deploy contract로 수용할 수 있다.
- CI provider나 registry provider를 먼저 선택하지 않아도 미래 image automation을 추가할 수 있다.
- ADR-0029의 표시 용어 개선은 유지하지만 기존 namespace를 영구 stable로 해석하지 않는다.

## Non-goals

이 ADR 자체에서는 다음을 수행하지 않는다.

- 기존 env/service/module identifier 즉시 rename
- Platform/Unified vLLM image CI build 또는 publish
- GHCR/Harbor 설치
- SBOM/provenance/signature 자동 생성
- Kubernetes/KServe/llm-d/Dynamo 도입
- Deployment Target ID rename

## Related

- [ADR-0029: Canonical terminology와 stable identifier 분리](./0029-canonical-terminology.md)
- [자동화 경계](../09_cicd.md)
- [로컬 개발과 빌드](../07_local_dev_build.md)
- [설정 체계와 Source of Truth](../05_configuration.md)
- [모델 운영](../06_model_operations.md)
- [Canonical Terminology](../reference/terminology.md)
