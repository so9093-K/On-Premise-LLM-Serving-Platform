# Control Plane Bootstrap

`GET /admin/control-plane/bootstrap`는 first-party Admin Console이 첫 화면을 그리기 전에 현재 서버의 posture와 capability를 발견하는 browser-safe read contract다.

이 endpoint는 Admin Bearer key가 필요한지 확인하기 전에 호출되어야 하므로 의도적으로 인증을 요구하지 않는다. 대신 반환 범위를 release/version, Deployment Target capability, Access Profile, Configuration schema/revision, monitoring availability와 안전하게 계산 가능한 링크로 제한한다. Secret 원문·fingerprint, 내부 runtime URL, host filesystem path, raw environment는 반환하지 않는다.

Deployment capability는 `configs/deployment_targets.yaml`, Access Profile은 `configs/access_profiles.yaml`, 문서 경로는 `AppSettings.documentation`, Configuration revision은 현재 resolver가 각각 소유한다. Bootstrap 전용 설정 사본을 만들지 않는다.

Monitoring은 Deployment Target의 `runs_monitoring_stack`을 typed capability로 사용한다. Grafana가 실제 host-published인지도 기존 deployment/exposure contract에서 계산한다. `local`처럼 direct URL을 안전하게 만들 수 있는 경우에만 `links.grafana`를 제공하며, TLS/edge routing을 Gateway가 소유하지 않는 profile에서는 monitoring이 사용 가능하더라도 URL을 추측하지 않고 `null`을 반환한다.

Admin Console은 `deployment.features`를 기준으로 Runtime/Main Model action을 표시하고, `configuration.write_available`을 초기 write affordance 판단에 사용할 수 있다. 실제 mutation 직전에는 각 control surface의 Plan/Apply contract를 다시 사용해야 하며 Bootstrap 값만으로 mutation을 수행하지 않는다.
