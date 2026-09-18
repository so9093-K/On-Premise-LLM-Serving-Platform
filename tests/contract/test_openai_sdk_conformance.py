from __future__ import annotations

import asyncio

import httpx
from openai import AsyncOpenAI

from ai_model_serving.apps.gateway import create_gateway_app
from tests.unit.gateway.helpers import FakeGatewayClients, settings


async def _exercise_openai_sdk() -> None:
    """Exercise the public OpenAI-compatible surface through the official SDK.

    This deliberately uses the SDK serializer/parser over an ASGI HTTP transport.
    Direct Gateway tests already cover application branches; this contract catches
    wire-shape drift that only appears to a real OpenAI client.
    """
    gateway_clients = FakeGatewayClients()
    gateway_clients.main_llm.post_response = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1.0,
        "model": "local-main",
        "status": "completed",
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "ok", "annotations": []}
                ],
            }
        ],
        "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    }
    app = create_gateway_app(settings(), gateway_clients)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://gateway.test",
    ) as http_client:
        async with AsyncOpenAI(
            api_key="test-key",
            base_url="http://gateway.test/v1",
            http_client=http_client,
        ) as client:
            models = await client.models.list()
            assert any(model.id == "local-main" for model in models.data)

            chat = await client.chat.completions.create(
                model="local-main",
                messages=[{"role": "user", "content": "Say OK only."}],
                max_tokens=8,
            )
            assert chat.model == "local-main"
            assert chat.choices[0].message.content == "ok"
            assert gateway_clients.main_llm.last_path == "chat/completions"

            response = await client.responses.create(
                model="local-main",
                input="Say OK only.",
                max_output_tokens=8,
            )
            assert response.model == "local-main"
            assert response.output_text == "ok"
            assert gateway_clients.main_llm.last_path == "responses"
            assert gateway_clients.main_llm.last_payload["store"] is False

            embedding = await client.embeddings.create(
                model="local-embed",
                input=["hello"],
                encoding_format="float",
            )
            assert embedding.model == "local-embed"
            assert len(embedding.data) == 1
            assert len(embedding.data[0].embedding) == 768


def test_official_openai_python_sdk_conformance() -> None:
    asyncio.run(_exercise_openai_sdk())
