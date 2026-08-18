# local-main: 메인 모델

`local-main`은 외부 API에 노출되는 논리 모델 ID다. 하나의 고정 체크포인트를 뜻하지 않으며, 운영자는 Main Model 프로필을 전환할 수 있다.

실제 모델 ID·revision·입력 modality·context·출력 한도·runtime image·vLLM command의 유일한 기준은 [`configs/main_model_profiles.yaml`](../../../configs/main_model_profiles.yaml)이다. 현재 활성 profile은 `GET /admin/main-model`의 `observed_runtime`으로 확인한다. 이 문서에는 특정 profile의 upstream 사양이나 context 값을 복제하지 않는다.

Upstream 모델 카드의 이론 사양만으로 이 플랫폼의 GPU·이미지·모달리티 동작을 보장할 수 없다. profile의 compatibility 기록, boot canary, Runtime 검증 결과를 함께 확인해야 한다.
