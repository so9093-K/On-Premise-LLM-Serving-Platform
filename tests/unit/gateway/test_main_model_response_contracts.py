from __future__ import annotations

from jsonschema import Draft202012Validator

from ai_model_serving.main_model.control import OPERATION_STAGES
from ai_model_serving.openapi_contracts import load_contract_schema

from .helpers import FakeGatewayClients, TestClient, create_gateway_app, settings


def _validate(schema_name: str, payload: object) -> None:
    Draft202012Validator(load_contract_schema(schema_name)).validate(payload)


class ContractMainModelSidecar:
    profile = {
        "id": "gemma4-12b-unified-fp8",
        "display_name": "Gemma 4 12B Unified FP8",
        "served_model_name": "local-main",
        "upstream_model_id": "RedHatAI/gemma-4-12B-it-FP8-Dynamic",
        "revision": "67e53491df7a281623fa740de61307d5c542b7f4",
        "compatibility": {"status": "verified"},
        "capabilities": {"deployed_input": ["text", "image"]},
        "gateway_policy": {},
        "runtime_image": "registry.example/vllm@sha256:" + "0" * 64,
        "vram_fraction": 0.76,
    }

    async def main_model(self, *, observed: bool = True):
        assert observed is True
        return {
            "public_model": "local-main",
            "active_profile": self.profile,
            "last_known_good_profile": self.profile["id"],
            "previous_known_good_profile": None,
            "gate": "open",
            "runtime_state": "active",
            "profile_locked": False,
            "boot_profile": self.profile["id"],
            "last_operation": None,
            "stats": {},
            "runtime_image": self.profile["runtime_image"],
            "state_recovery_error": None,
            "observed_runtime": {
                "status": "ready",
                "container_state": "running",
                "health": "healthy",
                "profile_id": self.profile["id"],
                "observed_at": 1.0,
            },
        }

    async def main_model_profiles(self):
        return [{**self.profile, "active": True}]

    async def switch_main_model(self, profile, *, confirm_unverified=False, request_id=None):
        return {"operation_id": "41cf50bb-60b2-4dbc-b38a-7dd07da91d97", "status": "pending", "reused": False}

    async def main_model_operations(self):
        return {
            "items": [
                await self.main_model_operation("41cf50bb-60b2-4dbc-b38a-7dd07da91d97")
            ]
        }

    async def main_model_operation(self, operation_id):
        return {
            "id": operation_id,
            "requested_profile": self.profile["id"],
            "previous_profile": None,
            "client_request_id": None,
            "status": "validating",
            "stage": "validating",
            "error": None,
            "rollback_error": None,
            "created_at": 1.0,
            "updated_at": 2.0,
        }

    async def get_status(self):
        return {}


def test_main_model_admin_reads_match_checked_in_contracts():
    clients = FakeGatewayClients()
    clients.sidecar = ContractMainModelSidecar()
    client = TestClient(create_gateway_app(settings(), clients))

    status = client.get("/admin/main-model")
    assert status.status_code == 200
    _validate("main_model_status_response.schema.json", status.json())

    profiles = client.get("/admin/main-model/profiles")
    assert profiles.status_code == 200
    _validate("main_model_profiles_response.schema.json", profiles.json())

    operations = client.get("/admin/main-model/operations")
    assert operations.status_code == 200
    _validate("main_model_operation_list_response.schema.json", operations.json())

    operation = client.get("/admin/main-model/operations/41cf50bb-60b2-4dbc-b38a-7dd07da91d97")
    assert operation.status_code == 200
    _validate("main_model_operation_response.schema.json", operation.json())


def test_main_model_operation_contract_stages_match_runtime_grammar() -> None:
    schema = load_contract_schema("main_model_operation_response.schema.json")
    properties = schema["properties"]
    assert tuple(properties["status"]["enum"]) == OPERATION_STAGES
    assert tuple(properties["stage"]["enum"]) == OPERATION_STAGES
