# ADR-0023: 로컬 lifecycle 명령의 책임과 파괴 범위

## Status

Accepted

## Context

기존 로컬 명령은 환경 재생성, 검증, image build, 모델 다운로드와 실행을 한 bootstrap에
묶거나 같은 동작을 target별 alias로 반복 노출했다. 그 결과 명령 이름만으로 비용과
변경 범위를 알기 어려웠고, 새 runtime target이 추가될 때 orchestration이 분기됐다.

## Decision

- 공개 기본 흐름은 `setup → build → prepare → up/status/down`이다.
- `setup`은 도구와 target 설정, `build`는 선택 target에서 저장소가 소유한 image,
  `prepare`는 선택 Main Model, `up/down`은 실행 상태만 소유한다.
- `check`는 정적 계약과 결정론적 테스트를 소유하며 image build에 암묵적으로 포함하지
  않는다. `rebuild`는 `build`와 같은 범위를 Docker cache 없이 실행한다.
- `down-all`은 `.env`나 변경 가능한 Compose project name이 아니라 checkout의 Docker
  working-directory label과 PID file로 실행 리소스 소유권을 판정한다. image, volume과
  model cache는 보존한다.
- `reset`은 기본적으로 plan만 출력한다. 정확한 확인값이 있을 때 project-built local
  image, `.env`, 환경, runtime state와 repository-local model cache를 제거한다.
- global Hugging Face cache와 daemon-wide Docker prune은 프로젝트 lifecycle 범위가 아니다.
- CI·배포·장애 진단에 필요한 세부 명령은 유지보수 계층에 남기되, 공개 lifecycle과
  완전히 같은 alias는 두지 않는다.

## Consequences

- image 재빌드가 모델 재다운로드나 서비스 재기동을 유발하지 않는다.
- target별 차이는 deployment target catalog와 orchestration에서 해석되고 사용자는 같은
  명령을 사용한다.
- 전체 초기화는 한 명령으로 가능하지만 확인 전에는 아무것도 삭제하지 않는다.
- `first-run`, `build_all.sh`, 별도 clean preview/all target과 app/static/Metal 중복 alias는
  제거된다.

## Related

- [ADR-0013](0013-env-lifecycle-non-destructive-sync.md)
- [ADR-0020](0020-runtime-control-and-deployment-targets.md)
