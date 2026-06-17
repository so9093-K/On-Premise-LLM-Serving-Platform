FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml requirements.lock requirements.runtime.lock README.md VERSION ./
COPY src ./src
COPY configs ./configs
COPY specs ./specs
COPY contracts ./contracts
COPY model_cards ./model_cards
COPY scripts ./scripts

RUN python -m pip install --upgrade pip \
    && python -m pip install --requirement requirements.runtime.lock \
    && python -m pip install --no-deps . \
    && python -m spacy download en_core_web_sm \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 9400 9405

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; url = os.getenv('HEALTHCHECK_URL') or ('http://127.0.0.1:%s/health' % os.getenv('HEALTHCHECK_PORT', '9400')); urllib.request.urlopen(url, timeout=3).read()"

CMD ["python", "-m", "uvicorn", "ai_model_serving.apps.gateway_asgi:app", "--host", "0.0.0.0", "--port", "9400"]
