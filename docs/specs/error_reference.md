<!-- GENERATED FILE. DO NOT EDIT MANUALLY. -->
<!-- Source: src/ai_model_serving/errors.py (ERROR_STATUS) + configs/error_catalog.yaml + Chat contracts -->
<!-- Command: make render-runtime-assets -->

# Chat API 에러 레퍼런스

`/v1/chat/completions` 실패 응답을 읽는 기준이다. 통합 에러 코드 카탈로그가 아니라 Chat API 사용자 기준 문서다.

```json
{ "error": { "code": "...", "message": "...", "param": "...", "retryable": false, "request_id": "req_...", "debug": { "...": "..." } } }
```

| 필드 | 의미 |
|---|---|
| `code` | 안정적인 에러 식별자. 클라이언트 분기 기준. |
| `message` | 사람이 읽는 설명. 파싱 기준으로 쓰지 않는다. |
| `param` | 고쳐야 할 요청 필드. 없을 수 있다. |
| `retryable` | 같은 요청 재시도 권장 여부. 실제 응답값을 우선한다. |
| `request_id` | 로그 추적과 운영 문의용 ID. |
| `debug` | 원본 cause/upstream 상태 요약. 운영·내부 개발자가 즉시 원인 확인에 사용한다. 없을 수 있다. |

모든 에러 응답은 `code`/`request_id`를 각각 `X-Error-Code`/`X-Request-Id` 응답 헤더로도 그대로 반환한다(`x-request-id`를 안 보낸 요청도 에러 시 새로 발급된 request_id가 헤더로 에코되어 바디와 항상 일치한다). 운영 로그(구조화 접근 로그)도 이 헤더 값을 그대로 남기므로, 클라이언트가 보고한 `request_id`나 `code`로 로그를 직접 검색할 수 있다.

## 판단 기준

| 유형 | 대표 code | 사용자/클라이언트 행동 |
|---|---|---|
| 요청 수정 | `VALIDATION_ERROR`, `MODEL_CAPABILITY_MISMATCH`, `REQUEST_TOO_LARGE` | `param` 기준으로 payload를 고친다. |
| 인증/권한 | `UNAUTHORIZED`, `FORBIDDEN` | API key, token, 권한을 확인한다. |
| 모델 준비 | `MODEL_UNAVAILABLE`, `RUNTIME_NOT_READY`, `MAIN_MODEL_*` | `Retry-After`가 있으면 기다렸다가 재시도한다. |
| 모델/stream 처리 | `UPSTREAM_TIMEOUT`, `UPSTREAM_ERROR`, `UPSTREAM_SCHEMA_ERROR`, `PARSE_ERROR`, `STREAM_LIMIT_EXCEEDED` | `retryable=true`면 백오프 재시도. 반복되면 runtime/log/payload를 확인한다. |
| 내부 오류 | `INTERNAL_ERROR` | `request_id`로 운영 로그를 추적한다. |

## 422 구분

`code`는 큰 오류 유형이다. 예를 들어 `VALIDATION_ERROR`는 요청 검증 실패를 뜻하지만,
그 값만으로는 응답 형식 요청 문제인지, 메시지 구조 문제인지, 미디어 데이터 문제인지 알기 어렵다.
`param`은 같은 `VALIDATION_ERROR` 안에서 실제로 고쳐야 할 요청 필드를 좁혀 주는 2차 분류다.

예:

```json
{ "error": { "code": "VALIDATION_ERROR", "param": "response_format" } }
```

```json
{ "error": { "code": "VALIDATION_ERROR", "param": "image_url" } }
```

```json
{ "error": { "code": "VALIDATION_ERROR", "param": "tool_choice.function.name" } }
```

| param | 영역 | 예 |
|---|---|---|
| `response_format`, `response_format.json_schema` | 응답 형식 요청 | JSON mode/schema 요청이 잘못됨. |
| `messages`, `messages[0].role`, `messages[0].content`, `messages.content.type` | 메시지 구조 | role/content/content part 구조가 잘못됨. |
| `model`, `max_tokens`, `temperature`, `top_p`, `stop`, `stream_options`, `logprobs`, `top_logprobs`, `logit_bias`, `reasoning` | Chat 옵션 | 범위, 타입, 조합, 정책이 맞지 않음. |
| `tools`, `tools[0].function.name`, `tool_choice`, `tool_choice.function.name`, `tool_calls` | Tool 호출 | tool schema나 선택한 tool 이름이 맞지 않음. |
| `image_url`, `image_url.url`, `image_url.detail` | 이미지 입력 | URL/MIME/base64/크기/픽셀/animated image 정책 위반. |
| `input_audio`, `input_audio.format` | 오디오 입력 | format/base64/크기/magic-byte/capability 문제. |
| `video_url`, `video_url.url` | 비디오 입력 | URL/MIME/base64/크기/frame/capability 문제. |
| 없음 | 요청 전체 | 특정 필드 하나로 귀속하기 어려움. |

## 정상 요청 예시

텍스트:

```json
{
  "model": "local-main",
  "messages": [
    { "role": "user", "content": "안녕하세요. 요약해 주세요." }
  ],
  "max_tokens": 256
}
```

이미지:

```json
{
  "model": "local-main",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "이 이미지의 핵심을 설명해 주세요." },
        { "type": "image_url", "image_url": { "url": "data:image/png;base64,..." } }
      ]
    }
  ]
}
```

오디오:

```json
{
  "model": "local-main",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "이 오디오에서 들리는 내용을 요약해 주세요." },
        { "type": "input_audio", "input_audio": { "format": "mp3", "data": "..." } }
      ]
    }
  ]
}
```

비디오:

```json
{
  "model": "local-main",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "이 영상의 장면 변화를 설명해 주세요." },
        { "type": "video_url", "video_url": { "url": "data:video/mp4;base64,..." } }
      ]
    }
  ]
}
```

JSON 응답:

```json
{
  "model": "local-main",
  "messages": [{ "role": "user", "content": "JSON으로 결과를 반환해 주세요." }],
  "response_format": { "type": "json_object" }
}
```

스트리밍:

```json
{
  "model": "local-main",
  "messages": [{ "role": "user", "content": "짧게 답해 주세요." }],
  "stream": true,
  "stream_options": { "include_usage": true }
}
```

Tool calling:

```json
{
  "model": "local-main",
  "messages": [{ "role": "user", "content": "서울 날씨를 확인해 주세요." }],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "parameters": { "type": "object", "properties": { "city": { "type": "string" } } }
      }
    }
  ],
  "tool_choice": "auto"
}
```

## 에러 예시

`response_format.type` 값이 잘못됨:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "response_format.type is bogus; use one of: json_object, json_schema, text.",
    "param": "response_format",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

`json_object`를 요청했지만 메시지에 JSON 지시가 없음:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "response_format.type=json_object requires an explicit JSON instruction in messages; add a user or system message such as 'Return JSON.'.",
    "param": "response_format",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

`messages`가 비어 있음:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "messages must be a non-empty array.",
    "param": "messages",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

`messages[0].role` 값이 잘못됨:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "messages[0].role must be one of: system, user, assistant, tool.",
    "param": "messages[0].role",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

`max_tokens` 범위 초과:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "max_tokens must be less than or equal to 4096.",
    "param": "max_tokens",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

`stream_options`를 보냈지만 `stream=true`가 없음:

```json
{
  "model": "local-main",
  "messages": [{ "role": "user", "content": "hi" }],
  "stream_options": { "include_usage": true }
}
```

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "stream_options may only be provided when stream=true.",
    "param": "stream_options",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

`tool_choice.function.name`이 `tools` 목록에 없음:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "tool_choice.function.name is get_time; use one of the provided tools: get_weather.",
    "param": "tool_choice.function.name",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

이미지 MIME이 허용되지 않음:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "image_url MIME type is image/x-icon; use one of: image/avif, image/bmp, image/gif, image/jpeg, image/jp2, image/png, image/tiff, image/webp, image/x-tiff.",
    "param": "image_url",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

animated GIF를 이미지 입력으로 보냄:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "animated image/gif is not supported as image input; use video_url with data:video/gif for motion analysis.",
    "param": "image_url",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

`input_audio.data`에 data URL prefix가 들어감:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "input_audio.data must be raw base64 (no data: URL prefix).",
    "param": "input_audio",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

오디오 format과 실제 bytes가 맞지 않음:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "input_audio.data does not look like a valid mp3 stream; send mp3 bytes or set input_audio.format to the actual file format.",
    "param": "input_audio",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

활성 모델이 오디오 입력을 지원하지 않음:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "audio content parts are not enabled for the active main model profile; remove input_audio or switch to an audio-capable profile.",
    "param": "input_audio",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

비디오 MIME이 허용되지 않음:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "video_url MIME type is video/x-flv; use one of: video/avi, video/gif, video/jpeg, video/mp4, video/quicktime, video/webm, video/x-matroska, video/x-msvideo.",
    "param": "video_url",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

활성 모델이 비디오 입력을 지원하지 않음:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "video content parts are not enabled for the active main model profile; remove video_url or switch to a video-capable profile.",
    "param": "video_url",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

전체 JSON body가 한도를 넘음:

```json
{
  "error": {
    "code": "REQUEST_TOO_LARGE",
    "message": "Request body exceeds 100000000 bytes. Limit applies to the full JSON body, including base64 media; reduce media size or split the request.",
    "retryable": false,
    "request_id": "req_..."
  }
}
```

upstream이 원본 400 body를 반환함:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Upstream rejected the request for local-main with HTTP 400; check error.debug.upstream_body for the runtime reason and adjust the request.",
    "retryable": false,
    "request_id": "req_...",
    "debug": {
      "cause_type": "HTTPStatusError",
      "cause_message": "Client error 400 Bad Request",
      "upstream_status": 400,
      "upstream_body": "audio decoder failed"
    }
  }
}
```

모델 응답이 요청한 JSON schema를 만족하지 못함:

```json
{
  "error": {
    "code": "UPSTREAM_SCHEMA_ERROR",
    "message": "structured output did not match response_format.json_schema; simplify response_format.json_schema or increase max_tokens, then check error.debug for the model output reason if present.",
    "retryable": true,
    "request_id": "req_..."
  }
}
```

stream guard가 응답 byte 한도를 초과함:

```text
event: error
data: {"error":{"code":"STREAM_LIMIT_EXCEEDED","message":"stream emitted 1048577 bytes; limit is 1048576. Reduce max_tokens or retry without stream=true.","retryable":false,"request_id":"req_..."}}
```

## Chat에서 주로 보는 code

| code | HTTP | retryable | 의미 | 권장 조치 |
|---|---:|:---:|---|---|
| `UNAUTHORIZED` | 401 | ✗ | API 키 또는 인증 정보가 없거나 유효하지 않다. | 유효한 Authorization 헤더(API 키)를 포함해 다시 호출한다. |
| `FORBIDDEN` | 403 | ✗ | 인증은 되었으나 해당 작업을 수행할 권한이 없다. | 필요한 권한/프로파일로 호출하거나 운영자에게 문의한다. |
| `NOT_FOUND` | 404 | ✗ | 요청한 리소스(엔드포인트, operation 등)가 존재하지 않는다. | 요청 경로와 식별자를 확인한다. |
| `REQUEST_TOO_LARGE` | 413 | ✗ | 요청 body 가 허용 한도를 초과했다. | payload(이미지·오디오·비디오·문서) 크기를 한도 이하로 줄인다. |
| `MODEL_CAPABILITY_MISMATCH` | 422 | ✗ | 요청이 활성 모델이 지원하지 않는 기능을 요구한다(예 - 비활성 모달리티). | 활성 프로파일이 지원하는 입력/기능으로 요청을 맞춘다. |
| `VALIDATION_ERROR` | 422 | ✗ | 요청이 Gateway 입력 계약 검증을 통과하지 못했다. | error.param 이 가리키는 필드를 message 지시대로 수정한다. |
| `RATE_LIMITED` | 429 | ✓ | upstream rate limit 또는 로컬 admission 한도에 도달했다. | Retry-After 를 존중해 백오프 후 재시도한다. |
| `INTERNAL_ERROR` | 500 | ✗ | Gateway 내부 처리 중 예기치 못한 오류가 발생했다. | request_id 와 함께 운영자에게 문의한다. |
| `PARSE_ERROR` | 502 | ✓ | upstream 응답을 파싱할 수 없었다(유효하지 않은 형식). | 반복되면 런타임 상태·로그를 확인한다. |
| `UPSTREAM_ERROR` | 502 | ✓ | upstream 런타임이 오류를 반환했거나 통신에 실패했다. | 잠시 후 재시도한다. 반복되면 런타임 로그를 확인한다. |
| `UPSTREAM_SCHEMA_ERROR` | 502 | ✓ | structured output 생성이 요청한 json_schema 를 만족하지 못했다. | response_format.json_schema 를 단순화하거나 max_tokens 를 늘린다. |
| `CIRCUIT_OPEN` | 503 | ✓ | 연속 실패로 upstream 서킷이 열려 일시적으로 차단 중이다. | 잠시 후 재시도한다. |
| `MAIN_MODEL_CONTROL_UNAVAILABLE` | 503 | ✓ | 메인 모델 control plane(admin sidecar)을 사용할 수 없다. | 잠시 후 재시도한다. 반복되면 sidecar 상태를 확인한다. |
| `MAIN_MODEL_SWITCH_IN_PROGRESS` | 503 | ✓ | 메인 모델 프로파일 전환이 진행 중이다. | 전환 완료(operation 상태) 후 재시도한다. |
| `MODEL_UNAVAILABLE` | 503 | ✓ | 대상 모델 런타임을 현재 사용할 수 없다(미기동·축출 등). | 잠시 후 재시도하거나 런타임 상태(/admin/runtimes)를 확인한다. |
| `QUEUE_TIMEOUT` | 503 | ✓ | 동시 처리 한도로 대기열에서 시간이 초과됐다. | 잠시 후 재시도하고 동시 요청 수를 줄인다. |
| `RUNTIME_NOT_READY` | 503 | ✓ | 런타임이 아직 기동/준비 중이다. | 준비 완료 후 재시도한다(/health 와 런타임 상태 확인). |
| `STREAM_LIMIT_EXCEEDED` | 504 | ✗ | 스트리밍 응답이 chunk/byte 한도를 초과했다. | 출력 길이를 줄이거나 비스트리밍으로 재시도한다. |
| `UPSTREAM_TIMEOUT` | 504 | ✓ | upstream 런타임 응답이 제한 시간을 초과했다. | 잠시 후 재시도하거나 요청 크기·복잡도를 줄인다. |
