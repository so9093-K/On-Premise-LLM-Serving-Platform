from __future__ import annotations

import json

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML is required: pip install pyyaml') from exc

from .common import ROOT

# 파싱 자체가 게이트여야 하는 surface만 훑는다.
#
# 여기 있는 파일들은 배포본에 실려 나가거나 CI/컨테이너가 그대로 읽는 것들이라,
# 구문이 깨지면 배포 시점에야 터진다 -- 특히 grafana 대시보드, loki/prometheus
# 설정, .gitlab-ci.yml은 다른 어떤 check도 읽지 않으므로 여기가 유일한 방어선이다.
#
# 저장소 전체를 rglob으로 훑지는 않는다. 그렇게 하면 .claude/, .vscode/ 같은
# 에디터·에이전트 설정과 reports/runtime/의 호스트별 생성물까지 검사 대상이 되는데,
# 그건 이 프로젝트가 보증하는 계약이 아니다.
SCANNED_TREES = ('configs', 'ops', 'specs')

# 트리 안에 섞여 있는 생성물·다운로드 산출물. gitignore 대상이고 호스트마다 내용이
# 달라서, 훑으면 검증이 패키저의 로컬 상태에 의존하게 된다.
GENERATED_PARTS = frozenset({'model_cache', 'models', '__pycache__'})

SCANNED_FILES = ('.gitlab-ci.yml', 'version_manifest.json')

PARSERS = {
    '.json': json.loads,
    '.yaml': yaml.safe_load,
    '.yml': yaml.safe_load,
}


def iter_contract_documents():
    for tree in SCANNED_TREES:
        for path in sorted((ROOT / tree).rglob('*')):
            if path.suffix not in PARSERS or not path.is_file():
                continue
            if GENERATED_PARTS & set(path.relative_to(ROOT).parts):
                continue
            yield path
    for name in SCANNED_FILES:
        path = ROOT / name
        if path.is_file():
            yield path


def validate_json_and_yaml_parse() -> None:
    failures: list[str] = []
    for path in iter_contract_documents():
        try:
            PARSERS[path.suffix](path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, yaml.YAMLError, UnicodeDecodeError) as exc:
            failures.append(f'{path.relative_to(ROOT)}: {exc}')
    if failures:
        raise SystemExit(
            'unparseable contract document(s):\n  ' + '\n  '.join(failures)
        )
