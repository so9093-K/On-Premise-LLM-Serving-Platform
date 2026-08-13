# 리소스 문서

이 디렉터리는 현재 운영 지침이 아니라, GPU·memory·runtime resource 판단에 사용한
검증 이력을 보존한다. 현재 선언값은 `configs/gpu_budgets.yaml`,
`configs/main_model_profiles.yaml`, `configs/model_serving.yaml`을 기준으로 하고, 실제
배포 상태는 boot log, `nvidia-smi`, vLLM `/metrics`로 확인한다.

- `gpu_resource_requirements_48gb.md`: 48GB GPU에서 26B/12B 프로필을 검증하며 얻은
  resource 판단 이력과 실측값

문서 안의 과거 26B·20K 기준은 현 기본 profile의 운영값으로 사용하지 않는다.
