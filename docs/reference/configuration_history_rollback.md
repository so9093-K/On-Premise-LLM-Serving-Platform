# Configuration History and Rollback

Configuration Plane history는 `${PLATFORM_STATE_DIR}/config/history/`의 durable operation journal을 사용한다. 별도의 revision database를 두지 않는다.

Durable journal schema는 기존 v1 reader와의 release downgrade 호환성을 유지한다. rollback transaction도 저장 시 기존 `kind=configuration_apply`를 유지하고 optional `target_revision`으로 intent를 기록한다. History API projection에서만 이를 `configuration_rollback`으로 표현하므로 이전 release의 recovery reader도 새 rollback record를 읽을 수 있다.

## History

`GET /admin/config/history`는 raw journal file을 그대로 반환하지 않는다. 운영자에게 필요한 operation metadata, actor/request id, revision, reviewed changes와 verification만 projection하며 rollback snapshot과 내부 exception 문자열은 숨긴다.

조회는 최신 operation부터 cursor 기반으로 페이지한다. `limit`은 1~200이며 기본값은 50이다. journal record 하나라도 읽을 수 없거나 schema가 유효하지 않으면 부분 결과를 정상으로 반환하지 않는다.

## Rollback

Rollback은 revision 번호를 뒤로 이동시키지 않는다. 현재 revision에서 과거 operator override snapshot을 목표로 새 mutation을 만든다.

1. `POST /admin/config/rollbacks/plans`에 현재 `base_revision`과 과거 `target_revision`을 보낸다.
2. 서버는 durable journal의 stable snapshot evidence를 확인하고 현재 operator state와의 차이를 `set`/`reset` changes로 다시 계산한다.
3. 운영자는 effective before/after, source, shadowing, risk와 `plan_digest`를 검토한다.
4. `POST /admin/config/rollbacks`에 현재 ETag를 `If-Match`로, target revision과 plan digest를 body로 보낸다.
5. 서버는 plan을 다시 계산한 뒤 일반 Configuration mutation과 같은 persist-first → runtime apply → verify transaction을 실행한다.

예를 들어 revision 20에서 revision 15의 operator state로 rollback하면 성공한 state는 revision 21이다.

과거 effective 값을 그대로 복사하지 않는다. repository default 또는 deployment override가 그 사이 바뀌었을 수 있으므로 target revision의 **operator override snapshot**을 현재 precedence에서 다시 resolve한다.

Rollback plan은 history가 없는 revision, 현재/future revision, 상충하는 snapshot evidence를 fail-closed로 거부한다. 현재 schema에서 더 이상 editable하지 않은 key를 과거 snapshot이 요구하는 경우에도 일반 mutation validation을 우회하지 않는다.
