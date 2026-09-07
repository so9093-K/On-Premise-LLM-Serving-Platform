# ADR-0022: 요청 이벤트와 컨테이너 진단 로그의 수집 경계 분리

## Status

Accepted

## Context

Gateway와 Risk Adapter의 `http_request_completed`는 요청 ID, route, 상태, 지연과
오류 분류를 제공하는 플랫폼 이벤트다. 기존 구성은 이 이벤트도 stdout으로 출력한 뒤
Docker `json-file`을 Alloy가 다시 읽었다. Docker 로그 경로는 컨테이너 ID에 묶이므로
Docker API를 가진 admin-sidecar가 target manifest를 만들어야 했다.

이 결합은 full-stack에서는 동작하지만 admin-sidecar가 없는 static target에서는 요청
로그 전체를 잃는다. 반면 vLLM traceback과 컨테이너 시작 실패는 플랫폼이 생성하는
이벤트가 아니며 container stdout/stderr를 best-effort로 수집하는 것이 맞다.

## Decision

- Gateway와 Risk Adapter의 구조화된 요청 이벤트는 애플리케이션 소유 JSONL에 기록한다.
- Compose는 두 서비스에 동일한 `REQUEST_EVENT_LOG_DIR`을 주입하고 서비스 식별자에서
  파일명을 유도한다. 호스트의 `.runtime/request-events`가 영속 원본이다.
- 파일은 서비스별 10 MiB, 백업 5개로 제한한다. Alloy가 재시작해도 저장된 position부터
  이어 읽고, position이 없으면 남아 있는 파일의 처음부터 수집한다.
- Alloy는 JSON의 `service` 필드를 Loki label로 투영하고 요청 Dashboard는
  `job="application"`만 조회한다.
- 파일 sink를 설정하지 않은 app-only 실행과 파일 쓰기 실패는 stdout을 사용한다.
  관측 경로 실패가 serving 응답을 실패시키지 않는다.
- Docker LogPath manifest 수집은 full-stack에 유지하되 `job="docker"`인 vLLM 및
  컨테이너 진단 로그에만 사용한다. static target은 이 경로를 만들지 않는다.

## Consequences

- 요청 로그는 Docker container ID, Docker API와 admin-sidecar 존재 여부에 의존하지
  않아 Linux full-stack과 macOS Metal static에서 같은 방식으로 조회된다.
- 동일 요청 이벤트가 application과 Docker 두 경로로 Loki에 중복 적재되지 않는다.
- native MLX-VLM의 crash/stdout은 이 결정의 대상이 아니다. 필요하면 native supervisor
  로그로 별도 수집하며 요청 이벤트의 신뢰 경로에 섞지 않는다.
- 이 경로는 운영 진단 로그다. 저장 승인, 변조 방지, 원격 수신 확인을 보장하는
  규제·감사 원장으로 선언하지 않는다.
- 로그 필드, 마스킹과 인증·노출 정책은 변경하지 않는다.
