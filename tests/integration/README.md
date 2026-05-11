# Integration 테스트

현재 저장소의 자동 테스트는 대부분 unit/contract 중심이다. Docker, GPU, vLLM이 필요한 검증은 target host에서 `scripts/validation/runtime_validation.py`로 수행한다.

## 운영 검증 흐름

```bash
make compose-up
make ready
python scripts/validation/runtime_validation.py
make compose-down
```

Integration 테스트를 추가할 때는 외부 네트워크, GPU, Docker 상태에 따라 flaky해지지 않도록 기본 `make test`와 분리한다.
