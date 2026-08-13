"""Risk Adapter 앱 계약 테스트가 공유하는 fake client와 기본 설정."""

from __future__ import annotations

from ai_model_serving.errors import ServiceError
from ai_model_serving.settings import AppSettings, EmbeddingProfile, RuntimeEndpoint, SecuritySettings


class FakeDetectorClient:
    def __init__(
        self,
        label: str,
        ready: bool = True,
        fail: bool = False,
        usage: dict[str, int] | None = None,
        top_logprobs: list[dict] | None = None,
    ) -> None:
        self.label = label
        self.ready = ready
        self.fail = fail
        self.usage = usage
        self.top_logprobs = top_logprobs
        self.last_payload = None
        self.endpoint = RuntimeEndpoint("risk", "http://risk/v1", "risk", 1)

    async def post_json(self, path, payload):
        self.last_payload = payload
        if self.fail:
            raise ServiceError("UPSTREAM_TIMEOUT", "timeout", True, 504)
        response = {
            "id": "chatcmpl_risk",
            "object": "chat.completion",
            "created": 1,
            "model": payload["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self.label},
                    "finish_reason": "stop",
                }
            ],
        }
        if self.usage is not None:
            response["usage"] = self.usage
        if self.top_logprobs is not None:
            response["choices"][0]["logprobs"] = {
                "content": [{"token": self.label, "logprob": 0.0, "top_logprobs": self.top_logprobs}]
            }
        return response

    async def get_json(self, path):
        if not self.ready:
            raise ServiceError("MODEL_UNAVAILABLE", "not ready", True, 503)
        return {"object": "list", "data": []}


class FakeRiskClients:
    def __init__(
        self,
        prompt_label: str = "<SAFE>",
        prompt_fail: bool = False,
        prompt_usage: dict[str, int] | None = None,
        prompt_top_logprobs: list[dict] | None = None,
    ) -> None:
        self.prompt = FakeDetectorClient(
            prompt_label,
            fail=prompt_fail,
            usage=prompt_usage,
            top_logprobs=prompt_top_logprobs,
        )


def settings() -> AppSettings:
    endpoint = RuntimeEndpoint("x", "http://runtime/v1", "x", 1)
    embedding = RuntimeEndpoint("test-embed", "http://embed/v1", "test-embed", 1)
    return AppSettings(
        app_env="test",
        project_version="0.1.0",
        security=SecuritySettings(
            api_key_required=True,
            api_keys=frozenset({"test-key"}),
            internal_service_token="internal-test-key",
        ),
        gateway_timeout_seconds=1,
        risk_adapter_timeout_seconds=1,
        main_llm=endpoint,
        embedding=embedding,
        risk_prompt=RuntimeEndpoint("risk-prompt", "http://prompt/v1", "risk-prompt", 1),
        risk_adapter_base_url="http://risk",
        embedding_profiles={
            "test-embed": EmbeddingProfile(
                model="test-embed",
                service_key="embedding",
                upstream_model_id="test/embed",
                dimensions=(1,),
                default_dimensions=1,
            )
        },
        embedding_model_routes={"test-embed": "embedding"},
        default_embedding_model="test-embed",
        default_retrieval_model="test-embed",
    )


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer internal-test-key"}
