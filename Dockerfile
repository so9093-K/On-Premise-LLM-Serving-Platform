# digest 고정: vLLM 계열 이미지들과 동일한 원칙(태그는 재푸시로 바뀔 수 있음).
# 최신 3.12.13-slim으로 갱신하려면: docker pull python:3.12.13-slim &&
# docker image inspect python:3.12.13-slim --format '{{index .RepoDigests 0}}'
FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_CONFIG_ROOT=/app

WORKDIR /app

# runtime image는 runtime lock만 설치한다. 운영 스크립트(scripts/)와 governance
# 문서용 명세(specs/openapi.*.yaml, contracts/, docs/reference/)는 CI/release
# artifact의 책임이며 application image에 넣지 않는다. 아래 두 파일만 예외다:
# - specs/schemas: openapi_contracts.py::load_contract_schema()가 /docs(Scalar)
#   렌더링 시 이 JSON 스키마를 런타임에 직접 읽어 request schema/examples를
#   주입한다. 빠지면 /docs가 계약 스키마 대신 FastAPI의 제네릭 dict 스키마로
#   조용히 degrade된다.
# - ops/compose/full-stack.private-network.yaml: admin-sidecar가
#   runtime_topology.py::load_runtime_topology()에서 이 파일의 depends_on을
#   읽어 공유 GPU에서 vLLM 컨테이너 기동 순서(prerequisite serialization)와
#   VRAM admission budget을 계산한다. 빠지면 compose_path.exists()가 False라
#   에러 없이 start_prerequisites_by_service가 조용히 빈 dict가 되어, 순차
#   기동 가드가 아무 경고 없이 비활성화된다.
COPY pyproject.toml requirements.runtime.lock VERSION ./

# runtime lock과 build backend가 바뀌지 않는 한 애플리케이션 소스 수정은 이
# 의존성 레이어를 재실행하지 않는다. slim base에는 setuptools가 없으므로,
# pyproject.toml의 build-system 선언을 읽어 설치하고 아래 source layer는 build
# isolation 없이 wheel을 만든다. build backend 버전은 pyproject.toml만 수정하면
# 되며, 소스만 바뀔 때 PyPI에서 backend를 다시 받지 않는다.
RUN python -m pip install --requirement requirements.runtime.lock \
    && python -c 'import tomllib; from pathlib import Path; Path("/tmp/build-requirements.txt").write_text("\n".join(tomllib.load(open("pyproject.toml", "rb"))["build-system"]["requires"]) + "\n")' \
    && python -m pip install --requirement /tmp/build-requirements.txt \
    && rm /tmp/build-requirements.txt

COPY src ./src
COPY configs ./configs
COPY specs/schemas ./specs/schemas
COPY ops/compose/full-stack.private-network.yaml ./ops/compose/full-stack.private-network.yaml

# /var/lib/ai-model-serving은 Gateway가 desired state를 쓰는 경로다. 보통은 bind
# mount가 덮지만, 이미지가 이 경로를 소유하고 있어야 마운트 없이 띄웠을 때도 동작한다.
RUN python -m pip install --no-deps --no-build-isolation . \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app \
    && install -d -o appuser -g appuser /var/lib/ai-model-serving

USER appuser

EXPOSE 9400 9405

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; url = os.getenv('HEALTHCHECK_URL') or ('http://127.0.0.1:%s/health' % os.getenv('HEALTHCHECK_PORT', '9400')); urllib.request.urlopen(url, timeout=3).read()"

CMD ["python", "-m", "uvicorn", "ai_model_serving.apps.gateway_asgi:app", "--host", "0.0.0.0", "--port", "9400"]
