## Documentation Impact Checklist

- [ ] 모델 목록이 바뀌었는가? `configs/model_catalog.yaml`, `model_cards/*.json`, 모델 문서를 확인했다.
- [ ] API endpoint/schema가 바뀌었는가? OpenAPI, JSON Schema, endpoint 문서를 확인했다.
- [ ] risk detector/code가 바뀌었는가? `configs/model_serving.yaml`, risk contract, schema를 확인했다.
- [ ] runtime/port/compose service가 바뀌었는가? 관련 실행 문서와 Compose projection을 확인했다.
- [ ] monitoring/Grafana target이 바뀌었는가? monitoring config, dashboards, projection 결과를 확인했다.
- [ ] CI/CD 또는 build target이 바뀌었는가? Makefile/scripts/CI 문서를 확인했다.
- [ ] 아키텍처 결정이 바뀌었는가? ADR 추가 또는 supersede 필요 여부를 확인했다.
- [ ] release-facing 변경인가? `CHANGELOG.md` 업데이트가 필요한지 확인했다.
- [ ] 생성 artifact가 변경됐는가? Source of Truth와 checked-in artifact를 함께 갱신했는가?
- [ ] docs 구조 또는 문서 lifecycle이 바뀌었는가? `docs/README.md`의 탐색 구조와 관련 문서를 함께 갱신했는가?
