# digest 고정: vLLM 계열 이미지들과 동일한 원칙(태그는 재푸시로 바뀔 수 있음).
# 최신 3.12.13-slim으로 갱신하려면: docker pull python:3.12.13-slim &&
# docker image inspect python:3.12.13-slim --format '{{index .RepoDigests 0}}'
FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_CONFIG_ROOT=/app

WORKDIR /app

# runtime image는 runtime lock만 설치한다. 계약·운영 스크립트와 명세 파일은
# CI/release artifact의 책임이며 application image에 넣지 않는다.
COPY pyproject.toml requirements.runtime.lock README.md VERSION ./
COPY src ./src
COPY configs ./configs

RUN python -m pip install --requirement requirements.runtime.lock \
    && python -m pip install --no-deps . \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 9400 9405

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; url = os.getenv('HEALTHCHECK_URL') or ('http://127.0.0.1:%s/health' % os.getenv('HEALTHCHECK_PORT', '9400')); urllib.request.urlopen(url, timeout=3).read()"

CMD ["python", "-m", "uvicorn", "ai_model_serving.apps.gateway_asgi:app", "--host", "0.0.0.0", "--port", "9400"]
