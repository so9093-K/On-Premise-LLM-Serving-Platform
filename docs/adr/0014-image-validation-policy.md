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

MIME type allowlist 검사(`image/jpeg`, `image/png`, `image/webp`, `image/avif`, `image/jp2`, `image/gif`, `image/bmp`, `image/tiff`, `image/x-tiff` 중 하나여야 함)는 유지된다. 변경 대상은 "MIME type으로 파서를 선택하는 것"이며, "MIME type을 검증하는 것"은 아니다. `image/gif`와 TIFF는 정적 image contract로 취급하고, animated GIF 전체의 시간적 변화는 `video/gif` `video_url` contract에서 다룬다. Multi-page TIFF는 정적 단일 이미지 계약 밖으로 보고 Gateway lightweight validator에서 거부한다.

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

## Related

- `configs/gpu_budgets.yaml` — 이미지 한도 single source-of-truth
- `src/ai_model_serving/contracts/media.py` — 파서 구현
- `docs/specs/api.md` — Vision 한도 API 문서 반영
- ADR-0003: All Major Models as vLLM Runtime (vLLM이 PIL auto-detect를 사용하는 배경)
