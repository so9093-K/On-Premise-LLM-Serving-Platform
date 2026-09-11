# ADR-0026: 성능 계약과 벤치마크 소유권

## Status

Accepted

## Context

이 플랫폼은 모델 교체, quantization 변경, speculative decoding, prefix caching,
scheduler 개선 같은 작업을 계속 하게 된다. 그때마다 "그래서 실제로 빨라졌는가"를
답하려면 성능을 측정하는 방식 자체가 먼저 합의되어 있어야 한다.

측정 체계를 먼저 정하지 않고 도구부터 만들면 다음이 벌어진다.

- 같은 `TTFT`라는 말이 측정 지점에 따라 다른 값을 뜻한다. client가 보는 것은
  사실 첫 chunk이고 첫 token이 아니다.
- 특정 장비에서 관찰한 값이 그대로 서비스 목표가 된다.
- 6개월 뒤 결과 파일을 열면 어떤 조건에서 나온 값인지 알 수 없다.
- prefix cache가 남은 상태에서 측정한 처리량이 실제보다 높게 보인다.

이 저장소에는 참고할 기존 제약이 이미 있다.

**플랫폼 코드는 target에 무관하고, 런타임 백엔드만 둘이다.** Gateway의 측정
경로(`logging_policy`, `metrics`, `upstream`, `gateway_service`)에는
`runtime_backend` 분기가 하나도 없다. target을 보는 곳은 `deployment_target`,
`settings`, `monitoring_projection` 세 모듈뿐이며 측정과 무관하다. 따라서 요청
지연, 토큰 수, admission 대기, streaming 종료 사유는 Linux와 macOS에서 같은 코드가
같은 방식으로 만든다.

갈라지는 것은 **런타임 내부 지표뿐이다.** `linux-nvidia-*`는 vLLM이 `vllm:*`를,
`macos-metal-static`은 native MLX-VLM이 `mlx_runtime_*`를 내며 이름이 겹치지
않는다. DCGM GPU 지표는 Linux에만 존재한다.

**Gateway admission이 동시성을 먼저 제한한다.** 현재 한도는 Linux main 3,
embedding 2, macOS main 1이다. 이 한도를 넘겨 부하를 주면 런타임 scheduler가
아니라 Gateway 큐를 측정하게 된다.

**Gateway가 요청 파라미터를 화이트리스트로 막는다.** profile의
`supported_parameters`에 없는 필드는 422로 거부된다. 실제로 macOS profile에
`stream_options`가 없어 streaming 응답에 usage가 오지 않았고, 그 결과 요청 로그의
토큰 수가 비어 있었다.

**Prometheus histogram이 조용히 틀릴 수 있다.** `http_request_duration_seconds`가
응답 헤더 시점에 값을 읽고 있어, 1초짜리 streaming 요청을 0.34ms로 기록했다.
읽는 사람이 없어 수개월간 드러나지 않았다.

**이 저장소의 반복 결함은 "한 사실을 여러 곳이 각자 말하는 것"이다.** 기본 env
파일 경로, 로컬 환경 집합, 허용 exposure audience 목록, YAML 로더가 모두 같은
방식으로 갈라져 있었다. 성능 계약은 metric 이름, workload id, SLO 키라는 새 사실을
대량으로 도입하며 config, schema, runner, evaluator, dashboard, validator 여섯
계층이 이를 참조한다.

## Decision

### 1. 네 개념을 분리한다

| 개념 | 의미 | Source of Truth |
|---|---|---|
| Metric contract | 각 지표가 무엇을 어떻게 재는지 | repository config |
| SLO | 서비스가 제공하려는 품질 목표 | repository config |
| Baseline | 특정 조건에서 관찰된 known-good 성능 | 명시적으로 승격된 artifact |
| Benchmark result | 특정 시점의 측정값 | 실행 산출물 |

**Baseline은 SLO가 아니다.** 관찰값에 여유율을 곱해 SLO를 만들지 않는다. SLO는
제공하려는 품질이고 baseline은 특정 하드웨어·모델·런타임 조합에서 나온 관찰이다.

### 2. 계약은 두 층으로 나뉘고, 대부분은 target에 무관하다

**Client/Gateway 층은 target 무관이다.** 이 층의 지표는 공유 코드가 만들므로
projection이 필요 없고, 결과를 target 간에 직접 비교할 수 있다.

```
client_time_to_first_chunk_seconds, client_time_per_output_chunk_seconds
client_operation_duration_seconds, client_request_success_ratio
gateway_admission_wait_seconds, client_goodput_requests_per_second
```

**Runtime 내부 층만 projection이 필요하다.** 이 층의 metric 이름은 백엔드 어휘를
쓰지 않는다. 순서를 뒤집어 `vllm:*`를 계약 이름으로 삼으면 MLX target에서 계약이
성립하지 않는다.

```
vLLM raw metric ─┐
                 ├─→ platform canonical metric ─→ dashboard / benchmark
MLX raw metric ──┘
```

런타임이 해당 지표를 제공하지 못하면 계약은 그 target에서 그 지표를 `unsupported`로
선언한다. 값을 0으로 채우거나 다른 지표로 대체하지 않는다. GPU 지표처럼 target에
존재하지 않는 것도 같은 방식으로 선언한다.

이 분리의 실질적 효과는 **SLO가 client 층에서 정의되면 target 무관하게 유효하다**는
것이다. runtime 내부 지표는 진단 근거이지 SLO의 기준이 아니다.

### 3. client는 chunk를 재고 runtime은 token을 잰다

사용자 체감 지연의 canonical 측정 지점은 benchmark client다. 다만 **client가 재는
것은 토큰이 아니라 chunk다.** client는 소켓에 도착한 chunk 경계만 볼 수 있고 그
안에 토큰이 몇 개인지 알 수 없다. OpenTelemetry GenAI semantic conventions가
client에는 `time_to_first_chunk`/`time_per_output_chunk`를, server에는
`time_to_first_token`/`time_per_output_token`을 두는 이유가 같다.

따라서 계약은 관찰 가능한 것만 그 이름으로 부른다.

| 층 | 관찰 단위 | 지표 |
|---|---|---|
| client | chunk | `client_time_to_first_chunk_seconds`, `client_time_per_output_chunk_seconds` |
| gateway | chunk | `gateway_time_to_first_chunk_seconds` |
| runtime | token | `runtime_time_to_first_token_seconds`, `runtime_time_per_output_token_seconds` |

`client_time_per_output_token_seconds`는 응답 `usage`의 토큰 개수로 나눈
**근사값**이며 계약이 그렇게 선언한다. 한 chunk가 여러 토큰을 나르면 chunk 기준
값과 갈라진다. 이 플랫폼의 macOS profile은 MTP speculative decoding을 기본으로
켜므로 이 차이가 실제로 발생한다.

token 기준 실측이 필요하면 runtime 층을 본다. 그 층은 vLLM만 제공한다.

### 4. 벤치마크 판정의 percentile은 raw per-request sample에서 계산한다

Prometheus histogram은 bucket 설계에 정확도가 좌우되고, 이 저장소에서 실제로
틀린 값을 낸 적이 있다. 결과 파일은 요청별 원시 샘플을 보관하고 evaluator가
P50/P95/P99를 계산한다. Prometheus는 운영 대시보드의 근거로 남는다.

### 5. 대기 구간은 하나로 합치지 않는다

```
client → [gateway_admission_wait_seconds] → gateway
       → [runtime_queue_duration_seconds] → runtime → prefill → decode
```

Gateway admission 대기와 런타임 scheduler 대기는 원인과 대응이 다르다. 요청 로그는
이미 `queue_wait_ms`로 전자를 남긴다. 벤치마크 계약에서도 둘을 별도 지표로 둔다.

### 6. Workload는 요청 내용이 아니라 트래픽 모델까지 포함한다

workload 계약은 최소한 프로토콜, 프롬프트 분포, 출력 한도, 트래픽 모드(open-loop
request rate 또는 closed-loop concurrency), cache 정책, warmup/측정 구간, 필요
요청 파라미터, primary/secondary metric을 선언한다.

**cache 정책은 필수 필드다.** `cold`, `warm`, `controlled_reuse`를 구분하고 결과에
기록한다. 같은 서버에 반복 측정하면 이전 프롬프트가 prefix cache에 남아 처리량이
과대 측정된다.

**필요 요청 파라미터를 선언한다.** TPOT을 재는 workload는 output token 수가
있어야 하고, 이 Gateway에서는 `stream_options`가 profile의 `supported_parameters`에
있어야 usage가 온다. contract validator가 이 연결을 검사한다.

### 7. 부하 sweep은 admission 한도를 함께 선언한다

동시성 sweep은 Gateway `max_concurrency`를 넘는 순간 런타임이 아니라 admission
큐를 측정한다. sweep workload는 admission 한도를 함께 명시하거나, admission 한도
자체를 측정 대상으로 선언한다. 이를 명시하지 않은 sweep 결과는 런타임 용량의
근거로 쓰지 않는다.

### 8. 결과는 환경 지문 없이 유효하지 않다

결과 파일은 git commit, 계약 버전, deployment target, 모델 id와 revision, runtime
image digest, 런타임 버전, GPU 모델·수·메모리, 주요 런타임 플래그, workload
profile, seed, cache 정책, 시작 시각과 지속 시간을 포함한다. 지문이 없는 결과는
schema validation에서 거부한다.

### 9. 결과의 원본은 JSON이고 Markdown은 파생이다

`reports/performance/` 아래 실행 산출물은 저장소가 소유하지 않으며
`reports/runtime/`과 같은 성격이다. baseline은 명시적 승격을 거친 artifact만
저장소가 소유한다. 벤치마크 실행이 baseline을 자동으로 덮어쓰지 않는다.

```
benchmark run → reports/performance/ → review → 명시적 승격 → benchmarks/baselines/
```

### 10. SLO 적용 수준은 단계적으로 승격한다

`observe`(보고만), `warn`(경고, 성공), `release`(릴리스 자격 실패),
`required`(필수 게이트) 네 단계를 둔다. 새 SLO는 `observe`에서 시작한다.
PR마다 GPU 벤치마크를 돌리지 않는다.

### 11. 이름 규약은 기존 표준을 따른다

새 어휘를 만들지 않는다. 두 표준을 조합한다.

**Prometheus naming convention**이 형태를 정한다. snake_case, base unit을 접미사로
두고, 누계 counter는 `_total`로 끝낸다. 밀리초를 쓰지 않는다. 저장소의 기존 지표
(`http_request_duration_seconds`)와 vLLM이 모두 초를 쓴다.

**OpenTelemetry GenAI semantic conventions**가 의미를 정한다. 대응하는 semconv
이름이 있으면 계약이 `otel` 필드로 그 출처를 기록한다. 나중에 OTel exporter를
붙일 때 매핑을 다시 찾지 않아도 된다.

contract validator가 두 규약을 모두 강제한다. 단위와 이름 접미사 불일치,
밀리초 이름, 표준이 아닌 `otel` 값을 거부한다.

### 12. 계약이 이름을 단독 소유한다

metric 이름, workload id, SLO 키는 config가 단독으로 소유한다. runner, evaluator,
schema, dashboard, validator는 그 값을 복제하지 않고 참조한다. contract validator가
존재하지 않는 이름 참조를 거부한다.

이 결정이 없으면 이 저장소가 반복해서 겪은 드리프트가 새 서브시스템에서 재현된다.

### 13. 벤치마크 도구는 실행 backend이지 기준이 아니다

`vllm bench serve` 같은 도구는 measurement backend로 사용할 수 있다. 계약과 결과
schema는 플랫폼이 소유하며, 어떤 backend로 측정했는지를 결과에 기록한다.

### 14. 성능 검증은 기능 검증과 분리한다

```
make validate      정의가 옳은가
make test          동작이 옳은가
make ready-full    살아 있는가
make runtime-validate  실제 연동이 옳은가
make perf-*        충분히 빠른가
```

`make runtime-validate`에 성능 판정을 합치지 않는다. 두 실패의 의미가 다르다.

## Consequences

| Positive | Negative |
|---|---|
| 같은 지표 이름이 언제나 같은 값을 뜻한다 | 계약과 validator를 먼저 만들어야 측정을 시작할 수 있다 |
| client 층 SLO는 target 무관하게 한 번만 정의한다 | runtime 내부 지표는 target마다 지원 여부를 선언해야 한다 |
| 런타임을 추가해도 결과 schema가 유지된다 | canonical 이름과 backend projection을 이중으로 관리한다 |
| 성능 회귀를 SLO와 baseline 두 기준으로 볼 수 있다 | baseline 승격이 사람의 리뷰를 요구한다 |
| 결과가 시간이 지나도 비교 가능하다 | 환경 지문 수집이 실행 전제조건이 된다 |
| 벤치마크 실패가 기능 실패와 구분된다 | 검증 계층이 하나 늘어난다 |

## Operational impact

- `make validate`에 성능 계약 검증이 추가된다. 하드웨어를 요구하지 않는
  결정론적 검사만 포함한다.
- `make perf-*` 명령군이 추가되며 실제 GPU 환경에서만 실행된다.
- `reports/performance/`는 gitignore 대상이다.
- `benchmarks/baselines/`는 저장소가 소유하며 변경이 리뷰 대상이다.
- 벤치마크가 의존하는 upstream 런타임 지표는 `configs/monitoring.yaml`의
  `required_metrics`로 선언한다. vLLM 지표는 upstream이 소유해 정적으로 존재를
  확인할 수 없으므로 두 단계로 나눈다. 계약의 projection이 그 선언 안에 있는지는
  `make validate`가, 선언한 지표가 런타임에 실제로 있는지는 runtime validation이
  확인한다. 둘을 합치면 오타와 upstream rename을 모두 잡는다.

## Migration notes

1. ~~canonical metric 이름과 backend projection을 확정한다.~~ 완료.
   `configs/performance/metrics.yaml`이 소유하며 `make validate`의
   `performance contract` 검사가 target·recording rule·exporter 선언과 대조한다.
   `linux-nvidia-static`은 monitoring stack을 띄우지 않아 runtime과
   infrastructure 층이 모두 `unsupported`다.
2. 성능 계약 config와 결과 JSON Schema를 추가하고 `make validate`에 계약 검증을
   넣는다. 이 단계에서는 벤치마크 요청을 보내지 않는다.
3. `interactive` workload 하나로 runner MVP를 만든다. 환경 지문 수집을 함께 넣는다.
4. Prometheus 스냅샷 수집과 evaluator를 추가한다.
5. 나머지 workload를 `batch → long-context → agentic` 순으로 추가한다.
6. baseline 승격 절차와 regression 판정을 추가한다.
7. 성능 dashboard를 추가한다.
8. GPU release qualification에 연결한다.

## Related

- ADR-0020: Runtime Control과 Deployment Target 분리
- ADR-0022: 요청 이벤트와 컨테이너 진단 로그의 수집 경계 분리
- ADR-0021: Configuration Plane과 Operator Override 경계
- `configs/monitoring.yaml`, `configs/deployment_targets.yaml`
- `ops/prometheus/rules/model_runtime.rules.yml`
