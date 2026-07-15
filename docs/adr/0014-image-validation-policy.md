# ADR-0014: Vision 이미지 검증 정책 — 한도 상향과 MIME type 독립 파서 탐지

## Status

Accepted

## Context

Gateway의 Vision 이미지 검증은 세 계층으로 구성된다.

1. **미들웨어**: raw HTTP body 크기 → 초과 시 HTTP 413
2. **계약 계층 (decoded bytes)**: base64 디코딩 후 바이트 수 → 초과 시 HTTP 422
3. **계약 계층 (pixels)**: width × height → 초과 시 HTTP 422

초기 한도는 보수적으로 설정되어 있었다.

- `max_image_bytes`: 750,000 bytes (≈730 KB)
- `max_image_pixels`: 1,048,576 (1024²)
- `max_request_body_bytes`: 1,250,000 bytes (≈1.2 MB)

두 가지 문제가 발견됐다.

**한도 일관성 문제**: base64 인코딩은 디코딩된 크기의 약 4/3배 overhead를 갖는다. `max_image_bytes = 750,000` 기준 base64 표현만 이미 ≈1,000,000 bytes인데, `max_request_body_bytes = 1,250,000`은 JSON 오버헤드를 더하면 실제로 한도에 근접하거나 초과하는 구조였다. 더 큰 한도를 설정하더라도 미들웨어가 먼저 413을 반환했다.

**MIME type / 실제 포맷 불일치**: 클라이언트가 `data:image/png;base64,/9j/4AAQ...`처럼 MIME type을 잘못 선언해도 실제 데이터는 JPEG일 수 있다. 기존 구현은 MIME type으로 파서를 선택했기 때문에, `_png_dimensions`가 JPEG magic bytes(`\xff\xd8`)를 만나면 `None`을 반환하고 422로 거부됐다. vLLM/PIL은 magic bytes 기반으로 포맷을 auto-detect하므로, 이 검증이 모델 동작을 제한하지도 않으면서 클라이언트 오류율을 높이는 역할만 했다.

**모델 아키텍처 기준**: Gemma 4 SigLIP2는 최대 8타일 × 896² = 6,422,528 픽셀이 아키텍처 상한이다. 초기 1,048,576 픽셀(1024²) 한도는 아키텍처 실제 상한의 약 16%에 불과해 사용 가능한 컨텍스트를 과도하게 제한했다.
(2026-07-09 정정: 이 "8타일" 근거는 부정확했다 — Update 섹션 참고.)

## Decision

### 이미지 한도 상향

| 설정값 | 변경 전 | 변경 후 |
|---|---|---|
| `max_image_bytes` | 750,000 | 7,000,000 (≈6.7 MB) |
| `max_image_pixels` | 1,048,576 | 6,422,528 (8타일 × 896², SigLIP2 상한) |
| `max_request_body_bytes` | 1,250,000 | 100,000,000 (≈95.4 MiB) |

한도 일관성 공식: `max_request_body_bytes ≥ ceil(max_image_bytes × 4/3) + ~100KB JSON`
- 7,000,000 × 4/3 ≈ 9,333,333 + JSON overhead → 10,000,000으로 충분히 수용.
- audio profile 활성화 시 decoded audio 25,000,000 × 4/3 ≈ 33,333,334 + JSON overhead → 100,000,000으로 수용.
- video profile 활성화 시 decoded video 50,000,000 × 4/3 ≈ 66,666,667 + JSON overhead → 100,000,000으로 수용.

### MIME type 독립 파서 탐지

`_image_dimensions(decoded)` 함수를 MIME type 인자 없이 magic bytes 기반 sequential detection으로 변경한다.

```python
# 변경 전: MIME type이 파서를 선택
def _image_dimensions(decoded: bytes, media_type: str) -> tuple[int, int] | None:
    if media_type == "image/png":
        return _png_dimensions(decoded)
    ...

# 변경 후: magic bytes sequential detection
def _image_dimensions(decoded: bytes) -> tuple[int, int] | None:
    for parser in (_jpeg_dimensions, _png_dimensions, _webp_dimensions, _avif_dimensions, _jp2_dimensions, _gif_dimensions, _bmp_dimensions, _tiff_dimensions):
        result = parser(decoded)
        if result is not None:
            return result
    return None
```

MIME type allowlist 검사(`image/jpeg`, `image/png`, `image/webp`, `image/avif`, `image/jp2`, `image/gif`, `image/bmp`, `image/tiff`, `image/x-tiff` 중 하나여야 함)는 유지된다. 변경 대상은 "MIME type으로 파서를 선택하는 것"이며, "MIME type을 검증하는 것"은 아니다. 정적 `image/gif`와 TIFF는 정적 image contract로 취급하고, animated GIF 전체의 시간적 변화는 `video/gif` `video_url` contract에서 다룬다. Animated GIF가 `image_url`로 들어오면 Gateway는 422로 거부하고 `video_url` + `data:video/gif` 사용을 안내한다. Multi-page TIFF는 정적 단일 이미지 계약 밖으로 보고 Gateway lightweight validator에서 거부한다.

### 단일 source-of-truth

이미지 한도 값의 source-of-truth는 `configs/gpu_budgets.yaml`이다. `model_catalog.yaml`, `model_serving.yaml`, `model_cards/local-main.json`은 이 값을 반영하며, 테스트(`tests/contract/test_runtime_policy.py`)는 cross-config 일치를 동적으로 검증한다.

## Consequences

| Positive | Negative |
|---|---|
| SigLIP2 아키텍처 상한까지 이미지를 처리할 수 있다 | 더 큰 이미지 허용으로 단일 요청 처리 시간이 늘어날 수 있다 |
| MIME type 오선언 클라이언트의 불필요한 422 제거 | (없음) |
| body 한도와 image/audio decoded 한도가 일관성 있게 정렬됐다 | |
| gpu_budgets.yaml 한 곳만 수정하면 모든 설정이 따라온다 | |

## Operational impact

- `max_request_body_bytes: 100,000,000`: Nginx/프록시의 `client_max_body_size`가 이 값보다 작으면 먼저 413을 반환한다. 100MB 이상으로 설정되어 있는지 확인이 필요하다.
- 이미지 처리 비용은 픽셀 수에 비례하므로, 6,422,528 픽셀(≈2688×2394) 이미지는 소형 이미지 대비 추론 시간이 증가한다. 현재 active runtime target에서는 이미지 토큰이 텍스트 예산을 함께 사용하므로, long-context와 image input canary를 같은 target policy에서 함께 확인해야 한다.
- `max_image_inputs: 1` 제한은 변경하지 않았다.

## Migration notes

한도 변경 외 코드 변경 사항:

- `src/ai_model_serving/contracts/media.py`: `_image_dimensions` 시그니처 변경 (인자 제거), 호출부도 동일하게 업데이트.
- `tests/contract/test_runtime_policy.py`: 하드코딩된 `== 750000` 단언을 `gpu_budgets.yaml` 동적 cross-check로 교체. `max_image_pixels` 단언 추가.

## Update (2026-07-09)

**"8타일 × 896² = 6,422,528" 아키텍처 상한 주장은 부정확했다.** 공식 Hugging Face `transformers` Gemma4 문서(`Gemma4ImageProcessor`/`Gemma4Processor`)를 확인한 결과, Gemma 4 vision은 고정 타일 그리드가 아니라 **`max_soft_tokens` (soft/vision token 예산) 기반의 동적 리사이즈** 방식이다.

- `max_soft_tokens`는 `{70, 140, 280, 560, 1120}` 중 하나만 허용하며 기본값은 280.
- 입력 이미지는 종횡비를 유지한 채 선택된 토큰 예산의 "patch budget"에 맞게 리사이즈된다(가로/세로는 48의 배수: patch size 16 × pooling kernel 3).
- 근사 픽셀 대응표: 70→~161K px, 140→~323K px, 280(기본)→~645K px, 560→~1.3M px, **1120(최대)→~2.6M px**.
- 위치 임베딩 테이블은 축당 10,240 포지션까지 지원하지만, 이는 "매우 큰 이미지도 좌표 인코딩은 가능하다"는 의미이지 실제 처리 해상도가 무제한이라는 의미는 아니다 — 실제 처리량은 여전히 `max_soft_tokens` 예산으로 제한된다.

즉 "8타일" 근거로 계산된 `max_image_pixels: 6,422,528`은 실제로는 모델이 가장 큰 토큰 예산(1120)에서 실제로 사용하는 픽셀(~2.6M)의 **약 2.5배**에 달한다. 이 초과분 픽셀은 모델이 버리는 게 아니라 애초에 이미지 프로세서가 리사이즈 단계에서 다운스케일하므로, 모델이 실제로 그 해상도를 "활용"하는 근거로는 상향을 정당화할 수 없다.

**`max_image_pixels`가 존재하는 진짜 이유를 다시 정리한다**: 모델 처리 해상도와 무관하게, 이 체크는 (1) 압축률이 매우 높은 이미지(예: 단색에 가까운 대형 PNG)가 작은 파일 크기로 거대한 픽셀 수를 주장해 이후 디코드 단계에서 과도한 CPU/메모리를 소모하는 "이미지 폭탄"을 차단하고, (2) 그 디코드 비용이 main_llm의 희소한 admission 슬롯 점유 시간을 늘려 다른 요청의 큐잉/타임아웃에 영향을 주지 않도록 상한을 두는 목적이다. `max_image_bytes`(파일 크기)만으로는 이 압축률 문제를 못 막는다.

이 기준으로 재검토한 결과, `max_image_pixels`를 6,422,528 → **12,845,056**(2배)로 올린다. 이 값은 여전히 실제 decompression-bomb 시나리오(통상 수억~수십억 픽셀)보다 몇 자릿수 작고, 절대값 자체도 ~4000×3200(평범한 사진 해상도) 수준이라 헤더 파싱 비용(`_jpeg_dimensions` 등 struct 기반, PIL 전체 디코드 아님)에 유의미한 부담을 주지 않는다. 디코드 비용/이미지 폭탄 방지라는 진짜 목적 기준으로는 2배 상향에 반대할 근거가 없다.

**`max_image_bytes`는 750,000 → 7,000,000 → 25,000,000으로 재상향한다.** 이건 픽셀 상한과 무관한 별개 축이다 — 압축 효율이 낮은 인코딩(예: 무손실 PNG)이 픽셀 수는 한도 이내여도 파일 크기만 큰 경우를 수용하기 위함이다. 한도 일관성 재확인: 25,000,000 × 4/3 ≈ 33,333,334 + JSON overhead → 기존 `max_request_body_bytes: 100,000,000`으로 이미 충분히 수용되어 별도 조정 불필요.

동기화 대상: `configs/gpu_budgets.yaml`, `configs/model_catalog.yaml`, `configs/model_serving.yaml`, `model_cards/local-main.json`, `docs/specs/api.md`.

### 비디오 한도 (`max_video_frames`, `max_video_frame_pixels`) 재검토

비디오 프레임은 동일한 이미지 프로세서를 거치는 정지 이미지로 취급되므로(`contracts/media.py`의 `video/jpeg` frame sequence·`video/gif` 검증이 각 프레임에 `_jpeg_dimensions` 등 이미지와 같은 픽셀 체크를 적용), 프레임당 픽셀 한도는 위 이미지 재검토와 같은 논리를 따른다: `max_video_frame_pixels`를 6,422,528 → **12,845,056**으로 올린다(픽셀 자체는 이미지와 동일 근거).

프레임 수(`max_video_frames`)는 이미지와 다른 근거로 접근했다. Google 공식 개발자 문서(ai.google.dev)에 따르면 Gemma 4는 **최대 60초 클립을 초당 1프레임(1fps)까지 처리**하도록 설계되어 있다. 즉 모델의 실제 처리 설계 지점은 "60초 @ 1fps = 최대 60프레임"이며, 기존 32는 이 설계 지점보다 낮게 잡혀 있었다. 이에 따라 `max_video_frames`를 32 → **60**으로 올려 모델의 실제 처리 창(window)에 맞춘다.

**주의 — 이미지와 달리 두 값은 요청당 곱셈으로 작용한다.** 이미지는 요청 하나에 정지 이미지 1장의 디코드 비용만 발생하지만, 비디오는 `프레임 수 × 프레임당 픽셀`만큼 전처리가 반복된다. 이번 변경으로 요청 하나의 최악 전처리 총량은 기존(32 × 6,422,528 ≈ 2.05억 px) 대비 새 값(60 × 12,845,056 ≈ 7.71억 px)으로 **약 3.75배** 늘어난다. 이는 main_llm 동시성 슬롯을 더 오래 점유시켜 다른 요청의 큐잉/503 확률에 영향을 줄 수 있는 트레이드오프이며, 실제 사용량(32프레임 한도에 자주 걸리는지)을 관찰하며 필요시 재조정한다.

동기화 대상: `configs/model_serving.yaml`, `docs/specs/api.md`. (비디오 한도는 이미지와 달리 `gpu_budgets.yaml`/`model_catalog.yaml`/`model_cards/local-main.json`에 별도 미러링되어 있지 않다 — 이미지처럼 별도 source-of-truth 통합이 되어 있지 않은 상태로, 향후 정리 대상.)

### `video/gif`는 프레임 수가 아니라 재생시간으로 게이트해야 한다

`max_video_frames`를 `video/gif`에 그대로 적용하면 실제로 잘못된 걸 측정한다. GIF는 인코딩 fps가 자유롭기 때문에, 예를 들어 10초짜리 클립을 15fps로 인코딩하면 raw 프레임 수가 150개가 되어 60프레임 상한에 걸려 거부된다 — 재생시간은 모델 설계 한도(60초)의 1/6밖에 안 되는데도. 반대로 60초 클립을 1fps로 인코딩하면 정확히 60프레임이라 통과한다. 즉 기존 체크는 "재생시간이 60초 이내인가"가 아니라 "GIF 파일 안에 인코딩된 프레임이 60장 이내인가"를 보고 있었다.

**수정**: `_gif_metadata`가 각 프레임의 Graphic Control Extension(label `0xF9`)에 있는 delay(1/100초 단위)를 합산해 실제 재생시간을 계산하도록 확장했다. `video/gif`는 이제:
1. **재생시간이 `max_video_duration_seconds`(60초) 이내인지**를 1차로 검사한다 — 이게 모델의 실제 1fps×60초 설계와 맞는 진짜 기준이다.
2. **프레임 수가 `max_video_duration_seconds × 30fps`(=1800) 이내인지**를 보조로 검사한다 — delay를 0에 가깝게 인코딩해 재생시간은 짧아 보이지만 실제로는 프레임이 매우 많은 degenerate 인코딩(디코드 비용 우회 목적)을 잡기 위한 backstop이다. 30fps는 통상적인 부드러운 애니메이션 GIF가 쓰는 상한선 근처 값으로 임의 선택했다(`contracts/media.py`의 `_GIF_FRAME_COUNT_FPS_CEILING`).

`video/jpeg`(클라이언트가 직접 프레임을 나열하는 형식)는 타이밍 메타데이터가 없어 이 재생시간 개념이 적용되지 않으므로 변경하지 않았다 — 기존처럼 `max_video_frames`(60)가 그대로 직접 적용된다.

새 설정 `max_video_duration_seconds: 60`을 `configs/model_serving.yaml`에 추가했다(현재 이미지처럼 별도 source-of-truth 미러링은 하지 않음, 위와 동일한 이유).

## Related

- `configs/gpu_budgets.yaml` — 이미지 한도 single source-of-truth
- `src/ai_model_serving/contracts/media.py` — 파서 구현
- `docs/specs/api.md` — Vision 한도 API 문서 반영
- ADR-0003: All Major Models as vLLM Runtime (vLLM이 PIL auto-detect를 사용하는 배경)
