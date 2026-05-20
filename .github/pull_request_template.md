## Documentation Impact Checklist

- [ ] 모델 목록이 바뀌었는가? `configs/model_catalog.yaml`, `model_cards/*.json`, 모델 문서를 확인했다.
- [ ] API endpoint/schema가 바뀌었는가? OpenAPI, JSON Schema, endpoint 문서, examples를 확인했다.
- [ ] risk taxonomy가 바뀌었는가? `configs/risk_taxonomy.yaml`, risk contract, examples, governance tests를 확인했다.
- [ ] runtime/port/compose service가 바뀌었는가? runtime 문서와 generated reports를 확인했다.
- [ ] monitoring/Grafana target이 바뀌었는가? monitoring config, dashboards, projection report를 재생성했다.
- [ ] CI/CD 또는 build target이 바뀌었는가? Makefile/scripts/CI 문서를 확인했다.
- [ ] 아키텍처 결정이 바뀌었는가? ADR 추가 또는 supersede 필요 여부를 확인했다.
- [ ] release-facing 변경인가? `CHANGELOG.md` 업데이트가 필요한지 확인했다.
- [ ] generated docs/reports를 재생성했는가?
- [ ] examples가 schema/taxonomy를 통과하는가?
- [ ] docs 구조 또는 문서 lifecycle이 바뀌었는가? `docs/manifest.yaml`을 갱신했는가?

참조: `docs/governance/document_management.md`의 Change Impact Matrix.
