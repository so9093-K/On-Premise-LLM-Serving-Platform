# ADR-0017: 선택 가능한 Gemma 4 메인 모델 런타임

날짜: 2026-06-18

## Status

Accepted

> **현재 운영 기준 (2026-08-12)**: Main Model의 기본 profile과 실제 실행 command는
> `configs/main_model_profiles.yaml`을, Gateway serving limit·parameter policy·runtime feature도
> 활성 profile의 `gateway_policy`를 기준으로 한다. 이 ADR의 다음 상태 분석은 선택형 profile 전환이 도입되기 전의 문제와
> 설계 배경을 보존하는 기록이다.

확장: [ADR-0018](0018-gpu-vram-admission-and-per-profile-runtime-image.md) — 통합 GPU
VRAM admission, per-profile 런타임 이미지, per-host util override, 오디오 활성화 경로.
0017의 "단일 고정 이미지·0.76 고정·오디오 inert" 전제를 갱신한다.

## 당시 상태 분석 (결정 시점)

- `main-llm-vllm`은 `ops/compose/full-stack.private-network.yaml`에서 시작하며, 26B 모델 커맨드는 현재 정적으로 고정되어 있습니다.
- `local-main`은 공개 API 식별자입니다. `configs/model_catalog.yaml`, `configs/model_serving.yaml`, Gateway 설정, 스키마, 모델 카드, 테스트, vLLM `--served-model-name` 인수를 통해 표출됩니다.
- Runtime Control은 현재 임베딩 및 리스크 런타임만 관리합니다. Gateway는 내부 Admin Sidecar를 호출하며, 읽기 전용 Docker 소켓 마운트는 사이드카만 받습니다.
- 기존 사이드카 작업은 기존 컨테이너를 시작·중지할 수 있지만, 다른 모델 커맨드를 적용할 수 없습니다. 따라서 메인 모델 전환은 허용 목록에 등록된 recreate 작업이 필요합니다.
- 기존 런타임 의도는 Gateway 메모리에만 존재합니다. 메인 모델 선택에는 사이드카 소유의 영속 상태가 필요합니다. 사이드카가 Docker 트랜잭션을 수행하고 관찰하기 때문입니다.
- 현재 26B 커맨드는 26B 프로파일로 바이트 단위로 보존됩니다: context 20000, 시퀀스 1개, 배치 토큰 20000, 최적화 레벨 3, GPU 활용률 0.76, Gemma 4 reasoning/tool 파서, 프로젝트 채팅 템플릿, prefix caching, xgrammar 구조화 출력.

## 검증된 업스트림 사실

- 26B 체크포인트 리비전: `8edbb9269ec9c3faad538ee1208a07eb46051f34`
- 12B 체크포인트 리비전: `67e53491df7a281623fa740de61307d5c542b7f4`
- 현재 호스트에서 사용 중인 런타임 이미지: `vllm/vllm-openai@sha256:f4492643056969529a74238f71dd66dc3097c0d433156a4f4478456bf84bd276`
- Google 문서에 따르면 Gemma 4 26B A4B는 텍스트/이미지 입력, 12B Unified는 텍스트/이미지/오디오 입력을 지원하며, 둘 다 텍스트를 생성합니다.
- RedHatAI 12B FP8 카드는 해당 체크포인트가 예비 단계이며 vLLM nightly 빌드 기준으로 테스트되었다고 명시합니다. 이는 이 배포 이미지가 모든 모달리티를 지원한다는 증거가 아닙니다.
- vLLM 문서와 Google 기능 문서는 이 Gateway의 오디오 요청 계약에 충분한 근거가 되지 않습니다. 따라서 오디오는 이번 변경에서 비활성 상태를 유지합니다.
- 현재 호스트는 48 GiB VRAM을 탑재한 NVIDIA RTX 6000 Ada Generation입니다. 실행 중인 26B 서비스는 정상 동작 중입니다.

## 결정

공개 별칭 `local-main`을 공유하는 두 개의 내부 프로파일을 추가합니다:

- `gemma4-26b-a4b-fp8`
- `gemma4-12b-unified-fp8`

Admin Sidecar가 소유하는 항목:

- 프로파일 허용 목록
- 부트 프로파일 우선순위 및 프로파일 잠금
- 원자적 상태 및 작업 이력
- 전역 전환 잠금
- Docker stop/remove/create/start 작업
- health, `/v1/models`, inference canary 검증
- 전환 실패 시 이전 프로파일 자동 복구

Gateway는 인증된 공개 관리 경계로서 프로파일 ID만 프록시합니다. 호출자로부터 모델 ID, 이미지, 커맨드, 환경 변수, Compose 경로를 절대 수락하지 않습니다. 전환 중이거나 복구 실패로 메인 모델 게이트가 닫힌 상태에서는 추론이 fail-closed됩니다.

교체 컨테이너는 기존 Compose 컨테이너 설정에서 좁게 선별된 하위 집합으로 생성됩니다. 이미지, 커맨드, 레이블, 마운트, 네트워크, GPU 장치 요청, 헬스체크는 프로파일에서 제어하거나 이미 허용 목록에 등록된 `main-llm-vllm` 컨테이너에서 복사합니다. 셸은 사용하지 않습니다.

## 부트 우선순위

1. `MAIN_LLM_PROFILE_LOCKED=true`: 설정된 부트 프로파일
2. 마지막으로 성공적으로 커밋된 활성 프로파일
3. `MAIN_LLM_BOOT_PROFILE`
4. 설정 오류

설치 기본값은 `gemma4-26b-a4b-fp8`을 유지하여 업그레이드 경로를 보존합니다.

## 전환 트랜잭션

1. 프로파일 및 readiness 정책 검증 (`request_switch`)
2. 전역 작업 잠금 획득 후 `preparing` 상태 영속화
3. 캐시 준비 (`backend.prepare`) 완료 직후 메인 모델 요청 게이트 닫기
4. `draining` — 설정된 drain 간격 대기 (boot reconcile 시 생략)
5. `stopping` — 상태 마커. `backend.replace()`가 현재 컨테이너 중지·제거·재생성·시작을 원자적으로 처리
6. `starting` — `backend.replace()` 실행 중 단계 표시
7. `validating` — `/v1/models`에 `local-main` 포함 여부 확인 및 최소 텍스트 canary 실행
8. 활성 프로파일 및 last-known-good 프로파일을 원자적으로 커밋하고 게이트 재개방
9. replace 이전 실패 시 (`replaced=False`): gate를 이전 상태로 복원하고 `failed` 종료
10. replace 이후 실패 시: `rolling_back` — 이전 프로파일로 `backend.replace()` + `validate()` 재시도
11. 자동 복구 성공: `failed` (게이트 재개방, 이전 프로파일로 서빙 재개)
12. 자동 복구 실패: `rollback_failed` (게이트 닫힌 채 유지, 양쪽 오류 모두 기록)

## 호환성 및 기능 정책

호환성은 `verified`, `likely`, `unverified`, `incompatible`, `unknown` 중 하나로 이유와 함께 `configs/main_model_profiles.yaml`에 기록합니다. 정적 메모리 임계값은 호환성의 증거가 아닙니다. 현재 profile 목록과 각 profile의 검증 근거는 이 ADR에 복제하지 않고 해당 catalog를 권위로 사용합니다.

당시 결정 기준으로 오디오는 12B의 모델 기능으로만 기록했고 배포된 제품 기능은 아니었습니다. 이 제약은 [ADR-0018](0018-gpu-vram-admission-and-per-profile-runtime-image.md)과 이후 media boot canary 설계로 대체되었습니다. 현재 계약은 active profile의 `deployed_input`과 media canary 결과로 audio/video를 gate합니다.

## 테스트 계획

- 프로파일 스키마, 리비전/이미지 고정, 중복 별칭 안전성
- 부트 우선순위, 잠금 동작, 원자적 영속성, 상태 손상 처리
- 알 수 없는/호환되지 않는 프로파일 거부 및 동시 전환 거부
- 가짜 Docker 백엔드를 사용한 Docker 트랜잭션 성공, 검증 단계 실패, 자동 복구 성공, 자동 복구 실패
- Gateway 관리 API 인증/프록시 동작 및 추론 게이트
- 기존 contract, OpenAPI, Compose, 릴리스, 문서 검사
- 실제 GPU 검증은 별도 보고하며 단위 테스트로 추론하지 않습니다

## 위험 요소

- Docker recreate는 서비스 중단을 유발합니다. 이는 draining 전환으로 무중단이 아닙니다.
- 이전 컨테이너 제거와 교체 사이에 프로세스 충돌이 발생하면 시작 시 복구가 필요할 수 있습니다.
- 12B 체크포인트 및 Gemma 4 런타임 지원은 예비 단계입니다.
- 고정된 이미지는 불변 digest이지만, 배포 환경은 전환 전에 해당 digest를 pull하거나 보유하고 있어야 합니다.

## 배포 통합

전체 Compose 시작은 잠금/설정/영속화된 부트 의도를 임시 생성 Compose 파일로 투영합니다. 이 파일은 추가 상태 저장소가 아니며, 활성 프로파일로 편집되지 않고, Compose 커맨드 완료 시 제거됩니다. Compose 설정 검증 전에 카탈로그, `.env` 잠금 정책, 원자적 사이드카 상태에서 재생성됩니다.

선택된 프로파일 스냅샷은 런타임 전환이 추론 게이트를 닫기 전 `preparing` 단계에서 공유 Hugging Face 캐시에 준비됩니다. 전체 배포도 서비스 변경 전에 동일한 준비를 수행합니다. 손상된 상태, 알 수 없는 영속 프로파일, 캐시 준비 실패, 또는 유효하지 않은 Compose 설정은 설치 기본값으로 조용히 부팅하는 대신 fail-closed됩니다.

## Update (2026-07-28)

`default_profile`을 `gemma4-26b-a4b-fp8`에서 `gemma4-12b-unified-fp8`로 바꿨다 -- 위
"설치 기본값은 gemma4-26b-a4b-fp8을 유지"라는 결정을 뒤집는다. 운영상 12B를 계속
서빙해야 하는 상황이 됐고, `default_profile`은 admin-sidecar가 한 번도 안 돈
최초 `docker compose up` 부트스트랩 시점에만 쓰이는 값이라(부트 우선순위 3번,
`MAIN_LLM_BOOT_PROFILE`) 실제로 12B를 계속 쓴다면 이 값도 12B와 일치해야 한다 --
어긋나면 최초 부트스트랩이나 compose가 main-llm-vllm을 재생성해야 하는 상황(예:
`docker compose up -d <다른 서비스>`가 `depends_on`으로 main-llm-vllm까지 함께
재생성하는 경우)마다 조용히 26B로 되돌아간다.

실제로 이 어긋남이 사고로 이어진 적이 있다: `MAIN_LLM_PROFILE_LOCKED=false`
상태에서 admin-sidecar 없이 compose가 main-llm-vllm을 재생성하면서(운영 실수로
`--no-deps` 없이 `docker compose up -d gateway`를 실행, `depends_on`으로
main-llm-vllm까지 재생성됨), 당시 아직 26B였던 `default_profile`의 compose
bootstrap placeholder로 컨테이너가 뜨는 바람에, `main-model-state.json`이 여전히
가리키던 12B(실제 서빙 의도)와 실제 컨테이너(26B)가 어긋났다. 그 결과
`reconcile_if_restarted()`가 매 10초 poll tick마다 12B 기준 audio canary를 이미
26B로 떠버린 컨테이너에 계속 보내 실패를 반복했다(`ValueError: ... does not have
an audio tower`) -- validate() 실패 시 backoff가 없어서 무한 재시도였다. 이 사고를
계기로 다음을 같이 고쳤다:

- `default_profile`을 실제로 계속 서빙할 프로필(12B)과 일치시킨다(이 Update).
- `ops/compose/full-stack.private-network.yaml`의 main-llm-vllm bootstrap
  placeholder(image/command)를 12B 기준으로 갱신하고, 이 placeholder가
  `default_profile`의 image/command와 항상 일치하는지 검증하는 governance
  테스트를 command까지 검사하도록 강화했다(이전엔 image만 비교해 command
  드리프트를 못 잡았다).
- `reconcile_if_restarted()`의 validate() 실패에 지수 backoff(10초 -> 최대
  5분)를 추가했다 -- drift 자체를 막지는 못해도, drift가 남아있는 동안 GPU
  엔진에 매 poll tick마다 무의미한 canary 요청을 영구히 반복하지는 않는다.
- 단일 서비스만 골라 재기동하는 `make compose-restart`(기본 `--no-deps`)를
  추가해, 관련 없는 서비스를 재기동하려다 `depends_on`으로 main-llm-vllm까지
  딸려 재생성되는 경로 자체를 없앴다.

`gemma4-26b-a4b-fp8`은 여전히 카탈로그에 `verified` 상태로 남아 있고 admin API로
전환 가능하다 -- 폐기된 게 아니라 기본값이 아니게 됐을 뿐이다.
