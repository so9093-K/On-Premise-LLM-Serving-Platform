# digest 고정: vLLM 계열 이미지들과 동일한 원칙(태그는 재푸시로 바뀔 수 있음).
# 최신 3.12.13-slim으로 갱신하려면: docker pull python:3.12.13-slim &&
# docker image inspect python:3.12.13-slim --format '{{index .RepoDigests 0}}'
FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS base

FROM base AS builder

# uv 실행 파일도 버전과 multi-platform manifest digest를 고정한다.
COPY --from=ghcr.io/astral-sh/uv@sha256:79c6f4776b851471cc73b7d21d0cc834bb94383c292e83640d27eff512864df7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 애플리케이션 소스보다 dependency layer를 먼저 만들어 source 변경 시 재사용한다.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-group quality --no-install-project

COPY src ./src
COPY README.md LICENSE NOTICE ./
RUN uv sync --locked --no-group quality --no-editable

FROM base

LABEL org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_CONFIG_ROOT=/app \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# runtime image는 uv.lock의 Platform dependency만 설치한다. 운영 스크립트(scripts/)와 governance
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
COPY --from=builder /app/.venv /app/.venv
COPY configs ./configs
COPY specs/schemas ./specs/schemas
COPY ops/compose/full-stack.private-network.yaml ./ops/compose/full-stack.private-network.yaml
COPY VERSION LICENSE NOTICE ./

# /var/lib/ai-model-serving은 Gateway가 desired state를 쓰는 경로다. 보통은 bind
# mount가 덮지만, 이미지가 이 경로를 소유하고 있어야 마운트 없이 띄웠을 때도 동작한다.
RUN install -d /usr/share/licenses/ai-model-serving-platform \
    && install -m 0644 LICENSE NOTICE /usr/share/licenses/ai-model-serving-platform/ \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app \
    && install -d -o appuser -g appuser /var/lib/ai-model-serving

USER appuser

EXPOSE 9400 9405

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; url = os.getenv('HEALTHCHECK_URL') or ('http://127.0.0.1:%s/health' % os.getenv('HEALTHCHECK_PORT', '9400')); urllib.request.urlopen(url, timeout=3).read()"

CMD ["python", "-m", "uvicorn", "ai_model_serving.apps.gateway_asgi:app", "--host", "0.0.0.0", "--port", "9400"]
