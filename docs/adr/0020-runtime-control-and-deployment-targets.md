# ADR-0020: Runtime Control과 Deployment Target 분리

## Status

Accepted; deployment state vocabulary updated by [ADR-0030](./0030-target-architecture-state-and-artifact-boundary.md).

## Context

기존 full-stack은 Linux, NVIDIA GPU, Docker Compose와 Admin Sidecar를 전제로 한다.
Gateway에서 Sidecar client는 선택적이지만 embedding과 Risk Adapter 설정, readiness,
공개 route 및 model listing은 전체 topology를 전제로 하므로 Sidecar URL을 비우는 것만으로
정식 static deployment가 되지 않는다.

macOS 지원을 운영체제 분기로 구현하면 serving 계약과 runtime lifecycle이 다시 결합된다.
또한 Ubuntu에서도 runtime lifecycle을 외부가 소유하는 static 구성이 필요할 수 있다.

## Decision

`configs/deployment_targets.yaml`을 deployment capability의 source of truth로 둔다.
각 target은 다음을 명시한다.

- `runtime_backend`: `vllm-cuda`, `mlx-vlm` 등 실제 inference backend
- `main_profile_catalog`: target이 사용하는 Main serving/runtime catalog
- `control_mode`: `sidecar` 또는 `static`
- `lifecycle_owner`: `platform` 또는 `external`
- `implementation_status`: `planned` 또는 `implemented`
- `qualification_status`: `verified` 또는 `unverified`
- `features`: API와 운영 기능의 활성 집합

기존 `validation_status`는 migration 기간의 legacy projection이다. 새 canonical config에는
사용하지 않으며, 실행 가능 여부는 `implementation_status`가 소유하고 실제 검증 근거는
`qualification_status`가 소유한다.

`static`은 macOS의 별칭이 아니다. static에서는 runtime lifecycle을 외부가 소유하고
Gateway는 고정 endpoint만 사용한다. 모델 switching, GPU admission, Docker reconciliation은
제공하지 않는다. 동일한 계약을 Linux CUDA와 macOS Metal target이 공유할 수 있다.

Deployment target은 다음 projection을 결정한다.

```text
Deployment Target
  -> target-specific Main profile catalog
  -> configured runtime endpoints
  -> required readiness dependencies
  -> Gateway route/OpenAPI surface
  -> /v1/models deployment model list
  -> runtime mutation availability
```

Runtime endpoint의 존재와 lifecycle 제어 가능 여부는 별개다. endpoint hostname이나
운영체제로 controllability를 추론하지 않는다. `configs/runtime_topology.yaml`은
feature와 runtime의 연결 및 `required`, `enabled`, `controllable`을 명시한다. 실제
Compose 서비스명과 포트는 `service_id`로 `configs/services.yaml`을 참조한다.

Runtime Control이 controllable runtime을 다시 시작할 때 지켜야 하는 순차 기동 관계도
`configs/runtime_topology.yaml`의 `start_prerequisites`가 소유한다. 이 값은 다른 runtime
key를 참조하며 Admin Sidecar가 직접 소비한다. Compose `depends_on`은 최초 deployment
boot에 필요한 추가 관계를 가질 수 있지만, controllable runtime 사이의 관계를 별도
정책으로 정의하지 않는다. 정적 validation은 Compose에 투영된 controllable dependency가
`start_prerequisites`와 일치하고 `condition: service_healthy`를 사용하는지 확인한다.

현재 Sidecar API 구조에서는 `runtime_control`, `model_switching`, `gpu_admission`이 하나의
원자적 control bundle이다. 세 플래그는 함께 켜거나 함께 꺼야 하며 governance validation이
이를 강제한다. API를 독립 router로 분리하기 전에는 부분 조합을 지원한다고 선언하지 않는다.

## Initial targets

- `linux-nvidia-dynamic`: 기존 full-stack. Sidecar와 전체 기능을 유지한다.
- `linux-nvidia-static`: 외부에서 기동한 CUDA Main runtime 하나를 Gateway가 사용한다.
  implementation은 `implemented`, qualification은 `unverified`이며 장시간·장문맥 검증이 남아 있다.
- `macos-metal-static`: native MLX-VLM runtime과 static Gateway 경로가 구현된 Main-only
  target이다. 모델·assistant revision과 실행 한도는 `configs/macos_mlx_runtime.yaml`,
  Python dependency와 lock은 `runtimes/mlx/`가 소유하며 M5 workload qualification은 별도 상태다.

## Consequences

- 기존 Linux/NVIDIA dynamic 동작은 default target으로 유지된다.
- optional feature가 없는 target은 해당 client, readiness dependency, public model과 route를 만들지 않는다.
- target-specific runtime 값은 환경 또는 향후 deployment manifest가 제공한다.
- Linux Sidecar의 Docker command catalog와 Mac native runtime catalog는 분리된다.
- Runtime Control의 재기동 순서는 deployment Compose를 역으로 파싱하지 않고 runtime topology 선언에서 결정된다.
- macOS target의 모델, context, concurrency, modality는 구현 설정과 실제 workload
  qualification 상태를 구분하며, 실측 전 profile compatibility를 `verified`로 선언하지 않는다.
