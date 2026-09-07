# ADR-0020: Runtime Control과 Deployment Target 분리

## Status

Accepted

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
- `validation_status`: `verified`, `implemented`, `planned`, `unvalidated`
- `features`: API와 운영 기능의 활성 집합

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

현재 Sidecar API 구조에서는 `runtime_control`, `model_switching`, `gpu_admission`이 하나의
원자적 control bundle이다. 세 플래그는 함께 켜거나 함께 꺼야 하며 governance validation이
이를 강제한다. API를 독립 router로 분리하기 전에는 부분 조합을 지원한다고 선언하지 않는다.

## Initial targets

- `linux-nvidia-dynamic`: 기존 full-stack. Sidecar와 전체 기능을 유지한다.
- `linux-nvidia-static`: 외부에서 기동한 CUDA Main runtime 하나를 Gateway가 사용한다.
  Gateway static 경로는 `implemented`이고, 장시간·장문맥 qualification은 남아 있다.
- `macos-metal-static`: native MLX-VLM runtime과 static Gateway 경로가 구현된 Main-only
  target이다. 모델·assistant revision, dependency lock, 실행 한도는
  `configs/macos_mlx_runtime.yaml`이 소유하며 M5 workload qualification은 별도 상태다.

## Consequences

- 기존 Linux/NVIDIA dynamic 동작은 default target으로 유지된다.
- optional feature가 없는 target은 해당 client, readiness dependency, public model과 route를 만들지 않는다.
- target-specific runtime 값은 환경 또는 향후 deployment manifest가 제공한다.
- Linux Sidecar의 Docker command catalog와 Mac native runtime catalog는 분리된다.
- macOS target의 모델, context, concurrency, modality는 구현 설정과 실제 workload
  qualification 상태를 구분하며, 실측 전 profile compatibility를 `verified`로 선언하지 않는다.
