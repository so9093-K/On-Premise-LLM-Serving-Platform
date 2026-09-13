#!/usr/bin/env bash
# 릴리스 artifact가 어느 소스에서 나왔는지 기록하기 위한 공통 조회.
#
# image build는 OCI label로, release ZIP은 매니페스트 파일로 같은 값을 싣는다.
# 두 곳이 각자 git을 호출하면 한쪽만 판정 기준이 바뀌어도 두 artifact가 서로
# 다른 출처를 주장하게 된다.
#
# 사용: source scripts/lib/source_provenance.sh; read_source_provenance
#       -> SOURCE_REVISION, SOURCE_STATE 를 설정한다.

read_source_provenance() {
  SOURCE_REVISION="unknown"
  SOURCE_STATE="unknown"
  if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    SOURCE_REVISION="$(git rev-parse HEAD)"
    if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
      SOURCE_STATE="dirty"
    else
      SOURCE_STATE="clean"
    fi
  fi
}
