# 변경 이력

이 파일은 사용자와 운영자에게 의미 있는 버전별 릴리스 노트만 기록한다. 긴 내부 유지보수 기록은 `docs/archive/changelog/`에 보존한다.

## [Unreleased]

### Added

- 에러 응답에 `error.param`(OpenAI 호환)을 추가했다. 검증 오류 시 문제 필드 경로를 담아, 클라이언트가 message를 파싱하지 않고 오류 출처를 구분할 수 있다 — 잘못된 출력 스펙은 `response_format`/`response_format.json_schema`, 잘못된 입력 데이터 포맷은 `input_audio.format`/`image_url`/`video_url`. 두 오류가 모두 `VALIDATION_ERROR 422`라 코드만으로는 구분되지 않던 피드백을 해소한다. 필드 범위가 아닌 오류에서는 생략되어 기존 응답과 호환된다.
- 생성 문서 `docs/specs/error_reference.md`(code·HTTP·retryable·의미·권장 조치 표)와 그 단일 소스 `configs/error_catalog.yaml`을 추가했다. status 권위는 `errors.py`의 `ERROR_STATUS`이며 contract 테스트가 양쪽 code 집합 일치를 고정한다.

- `local-main` 외부 alias를 유지하면서 Gemma 4 26B A4B FP8과 Gemma 4 12B Unified FP8 중 하나를 선택하는 메인 모델 프로필 제어를 추가했다. `GET /admin/main-model`, `GET /admin/main-model/profiles`, `POST /admin/main-model/switch`, operation 조회 API를 제공하며, 전환 상태와 마지막 정상 프로필을 atomic state file에 영속화한다. ([ADR-0017](docs/adr/0017-selectable-main-model-runtime.md))
- 메인 모델 전환에 drain, container recreate, health, `/v1/models`, 실제 text canary, last-known-good rollback 절차를 추가했다. 활성 프로필, 고정 model revision/runtime image, request gate, 전환·rollback 결과는 Gateway Prometheus metric으로 확인할 수 있다. 12B는 현재 고정 revision과 pinned derived runtime image 기준 1차 검증을 완료해 compatibility를 `verified`로 제공하며, audio/video 제품 입력은 active profile의 `deployed_input`과 media boot canary 통과 여부로 gate한다. ([ADR-0017](docs/adr/0017-selectable-main-model-runtime.md), [ADR-0018](docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md))
- 단일 GPU에서 메인·보조 모델의 VRAM을 단일 예산으로 보고 모든 모델 로드를 admission으로 통합했다. 비용은 정적 `gpu_memory_utilization` 합, 천장은 `configs/gpu_budgets.yaml`의 `avoid_above`(현재 0.93)이며 메인도 참가자(non-evictable)다. 초과 시 거부 + 축출 계획을 `409`로 반환하고 `force`로 자동 축출하며, 축출 순서는 `resource_control.criticality` 기반(임베딩 → risk, 메인 보존)이다. `GET /admin/runtimes`·`GET /gpu-budget`에 예산 스냅샷을 노출한다. ([ADR-0018](docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md))
- 메인 프로필별 런타임 이미지 오버라이드(`profile.image`)를 추가했다. 미지정 시 공용 `runtime.image`를 상속하며 digest-pin 필수다. 런타임 능력(예: 오디오 디코드 라이브러리)이 프로필을 따라오므로, 12B만 오디오 이미지를 핀하고 26B는 base 이미지를 그대로 둘 수 있다. ([ADR-0018](docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md))
- `vllm-gemma4-audio` derived 런타임 이미지가 12B Unified의 **이미지·오디오·비디오 입력**을 active-profile-gated 기능으로 제공한다. stock `gemma4-unified-cu129` base에서 12B는 이미지 요청에 pad-only 출력, 오디오는 멀티모달 warmup 크래시였다(텍스트만 정상). 2026-06-25 라이브에서 두 상류 버그로 규명했다 — 비전 투영 `vision_embedder.patch_dense`가 양자화 `ignore` 리스트의 HF 이름(`model.vision_embedder.patch_dense`)과 vLLM 내부 이름(`vision_embedder.patch_dense`) 불일치로 FP8 오양자화되고, vLLM이 요구하는 `feature_extractor.fft_length`가 transformers FE에 없다. `apply_gemma4_multimodal_patches.py`가 두 패치를 적용하고(상류 레이아웃에 assert), runtime image는 `soundfile`/`librosa`와 PyAV 기반 container decode stack을 더한다. 12B 프로필은 `image: ${AUDIO_VLLM_IMAGE}`로 핀하며, `build-vllm-derived`가 산출한 immutable digest를 `deploy-gpu-175`가 박스에서 pre-pull 후 `.env`에 자동 주입한다(`RISK_VLLM_IMAGE`와 동일, 수동 핀 불필요). 활성화는 `DEPLOY_MODE=full`로 release 파이프라인을 시작한 뒤 배포를 클릭하면 된다(GitLab 12.1.1은 `rules:` 미지원이라 ~25GB derived 빌드는 수동 opt-in이 불가피; deploy 자체는 `configs/main_model_profiles.yaml` runtime-sensitive 변경으로 자동 full). switch 시 AAC-in-MP4 `input_audio`와 MP4 `video_url` media boot canaries가 런타임 디코드를 검증해 실패하면 롤백하므로 12B가 half-capable로 라이브되지 않는다. 두 패치는 상류 버그이므로 머지 후 제거 대상이다. ([ADR-0018](docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md))
- 한국 전화번호(휴대폰 `01x-`, 서울 `02-`, 지역 `0[3-9]x-`)를 PII Protection detector가 D2 신호로 감지한다. 기존에 Presidio English recognizer가 한국 번호 포맷을 안정적으로 인식하지 못해 누락되던 케이스다.
- Anthropic API 키(`sk-ant-...`) 패턴을 Secret Exposure detector가 D4 신호로 감지한다. 이전에는 고엔트로피 generic candidate로만 잡혔다.
- `make sync-env` — `git pull` 이후 `.env`를 템플릿과 동기화한다. 누락 키를 추가하고 폐기 키를 제거하되, 기존 크리덴셜·이미지 태그·커스텀 값은 모두 보존한다. 시크릿을 재생성하지 않는다.
- `setup_env.py --env-file <path>` — `--sync-env` 실행 시 프로젝트 루트가 아닌 다른 경로의 `.env`를 대상으로 지정할 수 있다. 별도 배포 디렉터리의 `.env` 동기화에 사용한다.
- 문서 lifecycle, ownership, source-of-truth, 검증 방식을 추적하는 `docs/manifest.yaml`을 추가했다.
- 업스트림 admission 대기열 초과(`QUEUE_TIMEOUT`)와 circuit breaker 개방(`CIRCUIT_OPEN`) 503 응답에 `Retry-After` 헤더를 추가했다. `QUEUE_TIMEOUT`은 고정 5초, `CIRCUIT_OPEN`은 실제 남은 cooldown 시간을 반환한다. 이전에는 클라이언트가 재시도 시점을 추측해야 했다.

### Changed

- 생성 OpenAPI의 각 에러 응답이 전체 code enum 대신 해당 HTTP status로 실제 올 수 있는 code만 노출하고, description에 각 code의 의미·retryable을 함께 보여주도록 했다. Scalar/`/docs`에서 status→code→의미를 바로 읽을 수 있어 해석성이 개선된다. status↔code 매핑은 `errors.py`의 `ERROR_STATUS`에서 도출하므로 새 진실 소스를 만들지 않는다.
- Main LLM 부팅 정책을 locked profile → 마지막 성공 active profile → 설치 기본 profile 순으로 정의했다. 기본 profile은 기존 26B이며 `MAIN_LLM_PROFILE_LOCKED=true` 배포에서는 Runtime Control 변경을 거절한다. 전환 중 신규 chat 요청은 `503`과 `Retry-After`를 반환하고 rollback까지 실패하면 fail-closed 상태를 유지한다. ([ADR-0017](docs/adr/0017-selectable-main-model-runtime.md))
- 메인 모델 `gpu_memory_utilization`을 `MAIN_LLM_GPU_MEMORY_UTILIZATION`(optional, (0,1])로 호스트별 오버라이드할 수 있게 했다. 카탈로그 값은 기준 호스트 기본값이며, override는 런타임 command와 admission 비용(`vram_fraction`)에 동시 반영되어 둘이 어긋나지 않는다. fraction은 호스트 VRAM 비율이므로 더 작은 GPU는 더 큰 값을 설정한다. ([ADR-0018](docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md))
- vLLM 이미지를 `gemma4-0505-cu129`(custom feature-branch 빌드)에서 `gemma4-unified-cu129`(vLLM main 기반, 2026-06-03)로 교체했다. `gemma4-0505-cu129`는 `StructuredOutputsConfig.disable_any_whitespace` 필드를 지원하지 않아 컨테이너가 exit code 2로 종료됐다. `gemma4-unified-cu129`에서 `Gemma4ForCausalLM` 아키텍처 지원 및 신규 API 적용을 확인했다. ([ADR-0016](docs/adr/0016-xgrammar-disable-any-whitespace.md))
- Main LLM runtime target을 `gpu_memory_utilization=0.76`, `max_model_len=20000`, `max_num_batched_tokens=20000`, `optimization_level=3`로 정렬했다. ModelRegistry projection, compose validation, model card, catalog, docs, tests가 같은 runtime policy를 검증한다. FP8 Dynamic checkpoint와 `kv_cache_dtype=fp8_e5m2` 조합은 현재 runtime image에서 boot 단계에서 거부되어 active target에서 제외했다. ([ADR-0015](docs/adr/0015-main-llm-20k-o3-runtime-target.md))
- Vision/media 입력 한도를 Gemma 4 SigLIP2와 multimodal payload 기준으로 상향했다: `max_image_bytes` 750,000 → 7,000,000, `max_image_pixels` 1,048,576 → 6,422,528, `max_request_body_bytes` 1,250,000 → 100,000,000. Video profile 활성 시 decoded video는 50,000,000 bytes까지 허용한다. 한도 source-of-truth는 config와 contract 테스트가 cross-config 일치를 동적으로 검증한다. ([ADR-0014](docs/adr/0014-image-validation-policy.md))
- `max_image_bytes`를 7,000,000 → 25,000,000으로, `max_image_pixels`를 6,422,528 → 12,845,056으로 재상향했다. 동시에 ADR-0014의 "8타일 × 896² 아키텍처 상한" 근거가 부정확했음을 정정했다 — 공식 Hugging Face `transformers` Gemma4 문서 기준 실제로는 `max_soft_tokens`(70~1120, 기본 280) 토큰 예산 기반 동적 리사이즈이며, 최대 예산(1120)에서도 실사용 픽셀은 ~2.6M다. `max_image_pixels`는 모델이 실제로 그 해상도를 쓰는지가 아니라 디코드 비용/이미지 폭탄(decompression bomb) 방지가 진짜 목적이므로, 이 기준으로 재평가해 12,845,056(여전히 통상적 사진 해상도 수준이며 decompression-bomb 시나리오보다 몇 자릿수 작음)으로 올렸다. ([ADR-0014](docs/adr/0014-image-validation-policy.md))
- `max_video_frame_pixels`를 6,422,528 → 12,845,056으로(프레임도 동일 이미지 프로세서를 거치므로 이미지와 같은 근거), `max_video_frames`를 32 → 60으로 올렸다. Google 공식 개발자 문서 기준 Gemma 4는 최대 60초 클립을 1fps까지 처리하도록 설계되어 있어(60초 @ 1fps = 60프레임), 기존 32는 이 설계 지점보다 낮았다. 다만 비디오는 이미지와 달리 `프레임 수 × 프레임당 픽셀`이 요청당 곱셈으로 작용해 최악 전처리량이 약 3.75배 늘어나므로, 실사용 패턴을 관찰하며 필요시 재조정한다. ([ADR-0014](docs/adr/0014-image-validation-policy.md))
- `video/gif`의 프레임 수 상한이 실제로는 재생시간이 아니라 GIF 인코딩 fps에 좌우되는 버그를 고쳤다 — 짧아도 fps가 높으면 부당하게 거부되던 문제. `_gif_metadata`가 Graphic Control Extension의 delay를 합산해 실제 재생시간을 계산하도록 확장하고, 신규 `max_video_duration_seconds`(60초)를 1차 기준으로, 프레임 수는 `60초 × 30fps = 1800`을 degenerate 인코딩 방지용 보조 상한으로 삼는다. `video/jpeg` frame sequence는 타이밍 메타데이터가 없어 기존처럼 `max_video_frames`(60)를 그대로 적용한다. ([ADR-0014](docs/adr/0014-image-validation-policy.md))
- Vision 이미지 포맷 파서 선택을 MIME type 기반에서 magic bytes sequential detection으로 변경했다. MIME type allowlist(`image/jpeg`, `image/png`, `image/webp`) 검사는 유지되지만, 파서는 MIME type 선언과 무관하게 실제 바이트로 포맷을 판단한다. MIME type을 잘못 선언한 클라이언트의 불필요한 422가 제거된다. ([ADR-0014](docs/adr/0014-image-validation-policy.md))
- 공통 error code 계약에 `DETECTOR_DISABLED`, `STREAM_LIMIT_EXCEEDED`를 반영하고, Gateway가 Risk Adapter의 `DETECTOR_DISABLED` 410 envelope를 보존하도록 했다.
- retrieval 내부 embedding 호출이 `truncate_prompt_tokens`를 전달하도록 정리했다. 확인되지 않은 `truncation_side`는 silent no-op 대신 422 validation error로 처리한다.
- non-local `local_open`/`custom`/`internal_trusted` auth profile과 production `SKIP_PREFLIGHT=1` 경로의 운영 hard-fail 조건을 강화했다.
- 운영 배포 동작 변경 없이 retrieval contract의 project root 탐색 의존을 runtime settings에서 분리하고, 계층 import boundary를 AST 계약 테스트로 고정했다.
- `bootstrap.sh`(`make rebuild-full`)이 `EXPOSURE_MODE`와 `EXPOSURE_AUDIENCE`를 기존 `.env`에서 읽어 재초기화 후 복원한다. 이전에는 `AUTH_MODE`만 보존되고 `EXPOSURE_MODE`는 초기화됐다.
- `deploy_gitlab_compose.sh` CI/CD 배포 시 `.env` 이미지 참조 업데이트 직후 `make sync-env`를 호출해 신규 템플릿 키를 서버 `.env`에 자동 반영한다.
- Grafana 운영 대시보드 UX를 Serving Home 중심 drill-down으로 정리했다. API/Risk/Runtime 상세 패널은 collapsed row로 내리고, idle 상태의 실패류 패널은 scrape가 살아 있으면 0으로 읽히도록 보정했다. Risk는 A1/A2 detection을 명시 카드로 분리하고 중복 Risk Types 상세 그래프를 제거했으며, Dashboard contract는 `configs/monitoring.yaml`에서 선언해 validator가 검증한다.
- Model Runtime Deep Dive와 API Delay Details에 평균 응답 시간 패널을 추가했다. 평균은 histogram `_sum/_count` 기반으로 `$window`를 따르며, 기존 p95 패널은 tail latency 확인용으로 유지한다.
- GPU Capacity and OOM Risk, Serving Home, Model Runtime Deep Dive의 token throughput 단위를 `tok/s`로 고치고, container CPU는 percentage가 아니라 `vLLM container CPU cores used`로 표시한다.
- Safe access log에 `client_ip_hash`, `forwarded_for_present`, `forwarded_proto`를 추가했다. Prometheus metric label에는 raw client IP를 넣지 않고, IP 기반 abuse 분석은 log correlation으로 수행한다.
- ADR canonical 위치를 `docs/adr/`로 통합하고 root `adr/`는 더 이상 사용하지 않는다.
- 설명형 request examples 문서를 `docs/examples/requests.md`로 이동했다.
- `reports/refactor/current_*`에는 실제 current state, handoff, inventory만 남기고 과거 audit snapshot은 archive로 분리했다.
- `deploy_gitlab_compose.sh`가 배포 성공 직후 digest로 핀된 이미지(platform, vllm-gemma4-audio)에 안정적인 cosmetic `:deployed` 태그를 부여한다. digest pin은 `docker images`에서 `<none>`으로 보여 on-box 식별이 어려웠는데, 런타임 핀(`.env`의 `@sha256`)은 그대로 두고 표시용 태그만 매 배포마다 현재 이미지로 옮겨 단다(이전 이미지는 태그를 잃고 dangling prune으로 회수). 동작 영향 없는 가독성 개선이다.
- `request_validation_rejections_total`의 `reason` 라벨이 image/audio/video 요청 거부를 bytes·pixels·mime·frames·duration 단위로 세분화하도록 `validation_reason()`을 확장했다(이전엔 video/audio 거부가 전부 `request`로 뭉뚱그려 집계됨). Usage Today 대시보드의 "Rejected Requests" 패널을 단일 합계에서 reason별 breakdown으로 바꾸고, `upstream_errors_total`을 target·code별로 보여주는 "Upstream Errors by Code" 패널을 신규 추가했다.
- 12B(`gemma4-12b-unified-fp8`) 프로필의 `--max-model-len`/`--max-num-batched-tokens`를 20000 → 50000으로 올렸다. `--max-num-seqs`(2)·`--gpu-memory-utilization`(0.76)은 그대로 두고 실제 배포 서버에서 boot/health/`/metrics` 검증까지 완료했다. profiling이 커진 배치만큼 activation 메모리를 더 확보하면서 KV cache pool(`num_gpu_blocks`)이 20707→16638로 줄어, 엔진 VRAM 사용량은 오히려 35.2GiB→30.2GiB로 감소했다. ([ADR-0015](docs/adr/0015-main-llm-20k-o3-runtime-target.md))

### Fixed

- 메인 모델 전환/재배포 직후 `response_format: json_schema` 요청에서 xgrammar 제약 디코딩 Triton 커널(`apply_token_bitmask_inplace_kernel`)이 JIT 컴파일되는 게 배포 로그로 관측되어, `DockerMainModelBackend.validate()`의 text canary 직후 best-effort 구조화 출력 웜업 호출을 추가했다(실패해도 전환을 롤백시키지 않음). `validate()`는 명시적 전환·boot reconcile·중단 복구 세 경로 모두에서 호출되므로 이 한 곳으로 세 경로가 함께 커버된다. 이 fix는 로그에서 관측된 JIT 이벤트에 대한 예방 조치이며, 별도로 신고된 CSO classifier 타임아웃 사고(원인: 프롬프트-스키마 필드 불일치로 인한 evidence 배열 무한 반복, `finish_reason: length`로 재현 확인)와는 무관하다. ([ADR-0018](docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md))
- 위 구조화 출력 웜업이 admin-sidecar 프로세스 재시작(`initialize()`) 경로에서만 돌고, compose가 main-llm-vllm 컨테이너만 recreate하는 배포 경로(예: `main_model_profiles.yaml` 변경으로 인한 rolling→full 자동 승격)는 admin-sidecar를 안 건드려 웜업을 놓치는 잔여 gap이 있었다. `scripts/ops/ready_full.sh`의 `warm_inference_paths_best_effort()`에 동일한 `response_format: json_schema` best-effort 웜업 호출을 추가해 이 배포 경로를 커버했다. 문서가 대안으로 제시했던 "`make ready-full` 수동 실행" 우회법은 이 fix 이전엔 실제로 구조화 출력을 전혀 데우지 않아 틀린 정보였는데, 이제 사실이 됐다. ([ADR-0018](docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md))
- main-llm-vllm이 admin-sidecar 제어 API를 거치지 않고 재시작되는 경우(예: 운영자의 수동 `docker restart`)를 admin-sidecar가 영영 감지하지 못해 구조화 출력/미디어 웜업이 계속 빠지던 근본 gap을 수정했다. admin-sidecar에 10초 간격 reconciliation 루프(`reconcile_if_restarted()`)를 추가해, 컨테이너의 Docker `State.StartedAt`을 마지막으로 `validate()`했던 값과 비교하고 drift가 감지되면 gate를 닫지 않은 채로 `validate()`를 다시 돈다(재검증 자체는 수 초 내로 끝나므로 poll 간격만큼 트래픽을 막을 필요가 없다고 판단; tick당 비용이 미미해 간격은 짧게 잡았다). 진행 중인 전환·복구 작업과는 기존 락으로 자동 직렬화된다. `validate()`에는 tool-calling(`--enable-auto-tool-choice --tool-call-parser gemma4`)에 대한 best-effort 웜업도 함께 추가했다 — json_schema 웜업과 같은 Triton JIT 커널을 공유할 가능성이 있어 이미 중복일 수도 있지만, 별개 이벤트인지 확인된 바 없어 안전하게 추가했다. ([ADR-0018](docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md))
- `HTTPException` 기반 응답이 401이 아니면 무조건 `code: VALIDATION_ERROR`로 나가 status와 code가 모순되던 문제를 수정했다(예: 404가 `VALIDATION_ERROR`, 503이 `VALIDATION_ERROR`). `errors.py`의 `STATUS_DEFAULT_CODE`로 status에 맞는 code(404→`NOT_FOUND`, 403→`FORBIDDEN`, 503→`MODEL_UNAVAILABLE` 등)를 매핑한다. pydantic 검증 오류 메시지의 `body.` 위치 접두사도 제거해 필드명만 노출한다.
- json_schema/tool-calling 웜업 호출이 `response.raise_for_status()`를 부르지 않아 4xx/5xx 응답도 조용히 "성공"으로 넘어가던 버그를 수정했다. 4xx는 보통 요청 검증 단계에서 막혀 정작 예열하려던 bitmask 커널까지 못 가므로, 웜업이 됐다고 착각한 채 아무 로그도 안 남는 상황이 가능했다. 두 호출 모두 `raise_for_status()`를 추가하고(non-fatal 정책은 유지) 실패 시 경고 로그가 실제로 찍히는 회귀 테스트를 추가했다. reconciliation 루프의 재시작~재웜업 노출 창 설명도 "gate 폐쇄로 줄어드는 수 초"와 "poll 간격을 포함한 전체 노출 창(최대 10초+α)"을 뭉뚱그려 쓰던 걸 정정했다. `apply_token_bitmask_inplace_kernel`이 V1 model runner에서 예열되지 않는 근본 원인도 boot 로그(`Using V2 Model Runner` 로그 부재 + `jit_monitor` 타임스탬프)로 직접 재확인해 이전 "미확인" 표기를 정정했다. ([ADR-0018](docs/adr/0018-gpu-vram-admission-and-per-profile-runtime-image.md))
- `json_schema` structured output 요청에서 whitespace가 `max_tokens`까지 반복 생성되던 버그를 수정했다. xgrammar의 `any_whitespace` 기능이 중첩 배열 닫는 `]` 이후 `}` 전이를 막아 stuck state에 진입하던 문제다(vLLM PR #12744, #15316). non-stream 요청은 502 `UPSTREAM_SCHEMA_ERROR`, stream 요청은 200이지만 invalid JSON으로 나타났다. `StructuredOutputsConfig`의 `disable_any_whitespace: true` 필드로 해결했다. ([ADR-0016](docs/adr/0016-xgrammar-disable-any-whitespace.md))
- Main LLM `max_output_tokens`를 4096 → 8192로 상향했다. 복잡한 JSON Schema를 사용하는 structured output 요청이 `finish_reason: length`로 잘려 `UPSTREAM_SCHEMA_ERROR` 502를 유발하던 문제다. configs, model card, OpenAPI spec, JSON Schema, test 6개 파일에 분산된 하드코딩을 일괄 반영했다.
- `make validate` 중 OpenAPI contract 검증이 `ADMIN_API_KEY_REQUIRED` 미설정 환경에서 admin endpoint의 401 응답을 누락 감지하던 문제를 수정했다. validator가 strict auth env를 임시 적용해 spec을 생성한 후 복원한다.
- `MAX_REQUEST_BODY_BYTES`를 `.env` 템플릿에서 제거하고 `configs/model_serving.yaml`(`operational_limits.max_request_body_bytes`)을 단일 source-of-truth로 일원화했다. 배포 시 `.env`는 rsync에서 제외되는 영속 파일이고 env가 yaml보다 우선이라, 템플릿에 중복으로 박힌 값이 yaml을 가린 채 갱신되지 않아 `/opt/acl-ai-gateway/.env`가 여러 릴리스에 걸쳐 1.25MB에 고정돼 있었다(실제 이미지·오디오·비디오 요청이 body 단계에서 413으로 차단). 다른 사이즈 한도(`max_image/audio/video_bytes`, `max_retrieval_documents`)와 동일하게 yaml 전용으로 정렬했고, `MAX_REQUEST_BODY_BYTES`를 `setup_env.py`의 `RETIRED_ENV_KEYS`에 추가해 `make sync-env`(배포)가 기존 `.env`에서 해당 키를 제거하도록 했다. `settings.py`는 명시적 env override는 비상 수단으로 계속 존중한다. 이로써 기존의 STALE 화이트리스트 마이그레이션(`normalize_request_body_limit`) 기제는 불필요해져 삭제했다.
- `deploy_gitlab_compose.sh`가 `.runtime/gateway`를 배포 실행 계정(주로 root) 소유로 만들어 두던 문제를 수정했다. 이 디렉터리는 gateway 컨테이너가 non-root `appuser`로 `runtime-state.json`을 쓰는 마운트 대상인데, 소유권이 안 맞으면 admin-sidecar가 `PermissionError`로 기동에 실패했다(재현: gateway를 완전히 재생성하는 배포마다 발생). `ensure_gateway_runtime_dir`가 플랫폼 이미지 컨테이너 내부에서 디렉터리를 생성해 항상 이미지가 실제로 도는 UID로 소유되게 했다.
- 미디어 base64 검증이 개행·공백 포함 base64를 `422`로 거부하던 문제를 수정했다. 게이트가 `base64.b64decode(validate=True)`를 공백 정규화 없이 호출해, `base64 file.m4a`(CLI 기본 76칸 wrap)·MIME 인코더 출력 같은 정상 페이로드가 downstream(vLLM)은 받아들이는데도 게이트에서 먼저 차단됐다(`input_audio.data must contain valid base64.`). 공유 헬퍼 `_decode_media_base64`가 ASCII 공백만 관용하고 알파벳·패딩은 엄격히 유지(`validate=True`)하도록 image_url·input_audio·video 4개 디코드 지점에 적용했다. 패딩 누락·invalid 문자는 여전히 거부한다. 또한 `input_audio.data`에 `data:` URL 접두사가 들어오면(필드 형태 오용) 모호한 base64 에러 대신 `input_audio.data must be raw base64 (no data: URL prefix).`로 구체적 안내한다.

### Security

- Gateway에는 Docker socket을 추가하지 않고, 내부 Admin Sidecar만 allowlist된 profile ID를 고정 model ID, revision, image digest, vLLM command로 변환하도록 했다. 관리 요청으로 임의 image, command, environment, Compose path를 주입할 수 없으며 Gateway와 Sidecar 사이에는 내부 service token을 사용한다. ([ADR-0017](docs/adr/0017-selectable-main-model-runtime.md))

## [0.0.1] - 2026-05-20

### Added

- Gateway 중심의 chat, embedding, retrieval, risk signal API 계약과 운영 문서 기준선을 제공한다.
- 모델 catalog, model cards, runtime config, OpenAPI/JSON Schema, monitoring projection 검증 흐름을 포함한다.
- Docker/GPU full-stack 운영을 위한 compose, Prometheus, Grafana, runtime validation report 생성 흐름을 제공한다.

### Changed

- CHANGELOG는 짧은 release history로 유지하고, 기존 `0.1.0-rc.1` 내부 maintenance 기록은 `docs/archive/changelog/maintenance_journal_0.1.0-rc.1.md`로 이동했다.

### Security

- 인증/인가 동작은 이 문서 재구조화에서 변경하지 않았다.
