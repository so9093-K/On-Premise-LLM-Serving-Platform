"""성능 벤치마크 runner(ADR-0026).

계약은 configs/performance/가 소유하고, 이 패키지는 그 계약을 읽어 실행하고
결과를 specs/schemas/performance_run.schema.json에 맞춰 기록하는 소비자다.
metric 이름과 workload 정의를 여기서 다시 적지 않는다.
"""
