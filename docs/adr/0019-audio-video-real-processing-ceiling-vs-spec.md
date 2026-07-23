# ADR-0019: 오디오/비디오 실제 처리 한계와 공식 스펙 간 격차

## Status

Accepted

## Context

Gateway의 `configs/model_serving.yaml` (`main_llm.request_limits`)는 오디오/비디오를
byte 크기 기준(`max_audio_bytes`, `max_video_bytes`)으로만 제한하고, `audio_input_note`/
`video_input_note`는 이를 "static safety bounds"라고 설명해왔다. 실제로 vLLM/gemma4
runtime이 이 입력을 어디까지 처리하는지는 검증된 적이 없었다.

**오디오**: `transformers`의 `Gemma4UnifiedProcessor.__init__`은
`audio_seq_length=750`, `audio_ms_per_token=40`을 기본값으로 갖는다
(`750 * 40ms = 30,000ms = 30초`). vLLM의 `gemma4_mm.py._compute_audio_num_tokens`는
실제 오디오 길이에서 나온 토큰 수를 `min(t, audio_seq_length)`로 clamp한다 — 즉
**30초를 넘는 오디오는 앞부분 ~30초만 인코더에 들어가고 나머지는 통째로 버려진다.**
샘플링이 아니라 truncation이다. 라이브 테스트로 확인: 440Hz→1200Hz로 바뀌는 45초
합성 오디오를 보냈을 때, 변화 지점이 20초(30초 이내)면 정확히 답했지만 35초(30초
이후)로 옮기면 "17초"라는 존재하지 않는 지점을 답했다. 두 요청 모두 `prompt_tokens`가
~750 clamp 근방(809~811)에 고정됐다. **에러 없이 HTTP 200으로 그럴듯한 오답을 반환한다.**
이 30초 숫자는 Google의 공식 Gemma 4 model card(`ai.google.dev/gemma/docs/core/model_card_4`,
"Audio supports a maximum length of 30 seconds")와 정확히 일치한다 — 임의 추정이 아니라
검증된 공식 스펙이다.

**비디오**: 같은 model card는 "Video supports a maximum of 60 seconds assuming the
images are processed at one frame per second"라고 명시한다 (60프레임/60초 @ 1fps,
E2B/E4B/12B/26B-A4B/31B 전 라인업 공통). 하지만 현재 설치된 vLLM `0.25.1`의
`gemma4_mm.py`는 `_VIDEO_MAX_FRAMES = 32`라는 상수를 쓰고, `transformers`의
`Gemma4UnifiedVideoProcessor` 기본값도 `num_frames = 32`다. 이 32는 모델 아키텍처의
한계가 아니라 **edge 배포(예: 16GB급 E4B)를 안전하게 돌리기 위한 라이브러리 측 보수적
기본값**으로 보인다(참고: 커뮤니티 가이드 "At 32 frames, you'll push E4B's limits").
라이브 테스트로 확인: 40개의 실제 프레임을 담은 `video/jpeg` 요청을 보내고 "몇 프레임을
받았냐"고 물으면 모델이 "32"라고 답한다 — truncation이 아니라 전체 구간에서 균등
샘플링하지만, 상한이 32에서 멈춘다.

`num_frames`를 60으로 올리는 override(`--mm-processor-kwargs`)를 검토했으나 안전하지
않다: `get_mm_max_tokens_per_item`이 GPU 메모리/encoder cache 예산을 계산할 때 쓰는
`_VIDEO_MAX_FRAMES=32`는 이 override와 무관하게 하드코딩돼 있다. 실제 처리량만
60으로 올리면 encoder cache 예약 용량(32프레임 기준)을 초과하는 요청이 생기고,
`encoder_cache_manager.py`의 `can_allocate()`는 이런 경우 크래시 대신 `False`를
반환한다 — 즉 크래시가 아니라 **요청이 스케줄링되지 못하고 멈추거나 타임아웃**될
위험이 있다. `_VIDEO_MAX_FRAMES` 자체를 60으로 고치려면 설치된 vLLM 패키지를 패치한
커스텀 파생 이미지가 필요하다(12B 오디오/이미지 버그 수정 때 만든 `AUDIO_VLLM_IMAGE`와
동급 작업). 게다가 이 변경은 GPU 메모리 프로파일링에도 영향을 준다 — ADR-0015의
Update(2026-07-16) 기록처럼, dummy 프로파일링 입력이 커지면 activation 메모리 추정치가
커져서 KV cache pool(`num_gpu_blocks`)이 줄어드는 동일한 트레이드오프가 예상된다.

추가로, Gateway의 `max_video_frames`/`max_video_duration_seconds` 검증은
`contracts/media.py`에서 `video/jpeg`(프레임 배열)와 `video/gif`에만 적용되고,
실제로 흔히 쓰일 `video/mp4`/`webm`/`mkv`/`mov`/`avi` 컨테이너 경로는 byte 크기와
MIME 시그니처만 확인할 뿐 frame count나 duration을 전혀 검증하지 않는다.

## Decision

지금은 vLLM 패치나 Gateway의 정밀한 duration validation을 구현하지 않는다. 대신
실제 검증된 사실을 정직하게 문서화한다.

- **오디오**: 실제 처리 한계는 ~30초(공식 스펙과 일치)이고, 이를 넘으면 조용히 잘린다는
  사실을 `configs/model_serving.yaml`의 `audio_input_note`에 명시한다. Gateway
  runtime에 duration 거부/경고 로직은 추가하지 않는다 — 실사용에서 30초를 넘는 오디오
  요청 빈도가 낮을 것으로 판단했고, 필요 이상의 다중 포맷(wav/flac/mp3/ogg/m4a/aac)
  duration parser를 지금 만드는 건 과한 엔지니어링이라고 본다. 이 gap은 의식적으로
  감수한다.
- **비디오**: 공식 스펙(60초/60프레임 @ 1fps)과 현재 실제 처리량(32프레임, 이 배포의
  vLLM 0.25.1 기본값)이 다르다는 사실을 `video_input_note`에 명시한다.
  `max_video_frames: 60`은 공식 스펙과 일치하는 값이지 현재 실제로 처리되는 프레임
  수가 아니다. `_VIDEO_MAX_FRAMES` 패치(진짜 60프레임 지원)는 GPU 재프로파일링과
  이미지 빌드가 딸린 별도 작업으로 미룬다.
- 실제 컨테이너 포맷(mp4 등)에 duration/frame-count validation이 없다는 gap도
  같이 문서화하되, 지금 구현하지는 않는다.

## Consequences

| Positive | Negative |
|---|---|
| `audio_input_note`/`video_input_note`가 실제 동작과 일치해, 이 config를 읽는 운영자/개발자가 더 이상 잘못된 전제로 판단하지 않는다 | 클라이언트가 30초 넘는 오디오나 32프레임 넘게 필요한 비디오를 보내도 Gateway가 여전히 조용히 통과시키고, 최종 답변이 부분 처리된 입력 기준이라는 걸 API 응답 자체로는 알 수 없다 |
| 비디오 60프레임 지원에 필요한 실제 작업 범위(vLLM 이미지 패치 + GPU 재프로파일링)를 명확히 남겨서, 나중에 하기로 결정할 때 다시 조사할 필요가 없다 | 두 gap 모두 이번에 닫지 않아 실사용에서 silent truncation/under-sampling이 계속 발생할 수 있다 |
| 공식 model card 근거로 두 숫자(30초, 60초)를 검증해서, 이후 변경 시 비교 기준이 생긴다 | vLLM 버전이 바뀌면(`_VIDEO_MAX_FRAMES` 등) 이 문서의 실측값이 stale해질 수 있다 |

## Operational impact

없음 — 이번 결정은 문서/주석 정정뿐이며 runtime 동작은 바뀌지 않는다.

## Migration notes

- `configs/model_serving.yaml`의 `audio_input_note`/`video_input_note` 갱신.
- `docs/specs/api.md`의 오디오/비디오 테이블에 실제 처리 한계와 이 ADR 참조 추가.
- 향후 vLLM 버전 업그레이드나 커스텀 이미지 패치로 비디오 60프레임을 실제로 지원하게
  되면, 이 ADR에 Update 섹션을 추가하고 `main_model_profiles.yaml`의
  `gemma4-12b-unified-fp8.compatibility`에 실측 근거(GPU headroom, boot 검증)를
  남긴다 (ADR-0015/0018과 동일한 절차).

## Related

- [ADR-0015](0015-main-llm-20k-o3-runtime-target.md) — `--max-num-batched-tokens` 증가가 KV cache pool을 줄이는 동일한 트레이드오프 실측 기록
- [ADR-0018](0018-gpu-vram-admission-and-per-profile-runtime-image.md) — per-profile 커스텀 런타임 이미지 선례 (`AUDIO_VLLM_IMAGE`)
- `configs/model_serving.yaml` (`main_llm.request_limits.audio_input_note`/`video_input_note`)
- `configs/main_model_profiles.yaml` (`gemma4-12b-unified-fp8`)
- `src/ai_model_serving/contracts/media.py`
- Google Gemma 4 model card: https://ai.google.dev/gemma/docs/core/model_card_4
