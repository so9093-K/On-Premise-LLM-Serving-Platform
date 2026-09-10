# ADR-0025: 사용자 접근 Profile과 기존 환경의 명시적 전환

## Status

Accepted

## Context

ADR-0012는 application 인증과 Compose 노출의 Source of Truth를 분리했지만,
일반 사용자는 여전히 `AUTH_MODE`, `EXPOSURE_MODE`, `EXPOSURE_AUDIENCE`와 여러
bind 주소를 함께 이해해야 했다. 특히 기존 `local_open`은 인증 mode 이름이면서
`master_open + private_lan`을 요구해 사용자 의도와 내부 policy primitive가 다시
결합됐다.

새 코드를 배포했다는 이유만으로 기존 Ubuntu `.env`의 접근 범위를 바꾸면 안 된다.
반대로 기존 호환성 때문에 신규 설치까지 인증 없는 LAN 전체 진단 topology를 기본으로
두는 것도 허용하지 않는다.

## Decision

일반 lifecycle은 `ACCESS_PROFILE=local|private|edge`만 사용자에게 공개한다.
`configs/access_profiles.yaml`은 기존 auth/exposure/service 기준을 참조해 안전한 조합을
resolve하는 유일한 Source of Truth다.

| Profile | Host 접근 | Auth | 외부 경계 |
|---|---|---|---|
| `local` | loopback | local 개발용 무인증 | 없음 |
| `private` | LAN/VPN | Gateway·Admin·내부 token | 운영자 network/TLS |
| `edge` | loopback Gateway | Gateway·Admin token | 같은 host의 TLS edge |

세 profile 모두 `private_network` exposure를 사용한다. raw model runtime과 Prometheus,
Loki 같은 backend를 일반 Access Profile로 host-publish하지 않는다. `master_open`은 기존
진단·운영 호환성을 위해 Advanced/legacy exposure mode로 남기며, 자동 만료와 rollback을
갖추기 전에는 임시 진단 명령이라고 부르지 않는다.

`make setup TARGET=... ACCESS=...`이 사용자 진입점이다. 새 `.env`는 `local`을 기본으로
생성한다. 기존 `.env`에 `ACCESS_PROFILE`이 없으면 legacy/custom으로 판단하고 어떤
auth/exposure/bind 값도 자동 이관하지 않는다. 명시적으로 `ACCESS`를 지정하면 변경 계획만
표시하고 `CONFIRM=access`를 함께 지정해야 원자적으로 기록한다. API key와 다른 secret은
회전하거나 출력하지 않는다.

기존 `auth-*`, `exposure-*` 도구는 Advanced/legacy 호환 계층으로 유지한다.
Principal/AuthZ, OIDC, workload identity와 외부 PDP는 실제 다중 주체·권한 요구가 생길
때 별도 결정으로 다룬다.

## Consequences

| Positive | Negative |
|---|---|
| 신규 설치의 가장 쉬운 경로가 loopback 기본값을 사용 | 기존 profile과 새 profile을 이관 기간 동안 함께 지원 |
| 사용자 의도와 내부 auth/exposure primitive가 분리 | `.env`에 resolve된 값을 함께 기록하고 drift 검증 필요 |
| 기존 Ubuntu 설정은 명시적 적용 전까지 변하지 않음 | `edge`는 같은 host의 외부 TLS proxy가 별도로 필요 |
| 일반 profile에서 raw runtime/ops 공개를 구조적으로 차단 | `master_open` 즉시 제거는 하지 않음 |

## Operational impact

```bash
# 신규 환경
make setup TARGET=<id> ACCESS=local

# 기존 환경: 첫 실행은 plan-only
make setup TARGET=<id> ACCESS=private

# 계획 확인 후 적용
make setup TARGET=<id> ACCESS=private CONFIRM=access
```

## Migration notes

- `ACCESS_PROFILE`이 없는 `.env`는 그대로 실행한다.
- 기존 `local_open + master_open + private_lan`을 새 `local`로 자동 표기하지 않는다.
- profile 적용은 접근 관련 값 전체를 한 번에 기록한다.
- 적용 뒤 static target은 env validator, dynamic target은 Compose preflight가 profile drift를 거부한다.
- 기존 환경 확인은 legacy 보존과 선택 profile 전환을 한 번 qualification하면 충분하며
  Unified vLLM image나 model revision 변경으로 취급하지 않는다.

## Related

- ADR-0012: Auth/Exposure primitive 분리의 이전 결정
- ADR-0013: `.env` 비파괴 동기화
- ADR-0023: 로컬 lifecycle 명령 경계
- `configs/access_profiles.yaml`
- `scripts/config/setup_env.py`
- `scripts/platform_cli.py`
