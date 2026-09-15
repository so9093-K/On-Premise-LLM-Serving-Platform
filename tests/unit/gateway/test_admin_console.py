from __future__ import annotations

import re
from dataclasses import replace

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.settings import DocumentationSettings

from .helpers import FakeGatewayClients, TestClient, settings


def _client(*, docs_enabled: bool = True) -> TestClient:
    cfg = replace(settings(), documentation=DocumentationSettings(enabled=docs_enabled))
    return TestClient(create_gateway_app(cfg, FakeGatewayClients()))


def test_console_is_served_when_api_docs_are_disabled() -> None:
    client = _client(docs_enabled=False)

    response = client.get('/admin/console/')

    assert response.status_code == 200
    assert 'AI Model Serving Control Plane' in response.text
    assert client.get('/docs').status_code == 404


def test_console_index_uses_no_cache_and_browser_security_headers() -> None:
    response = _client().get('/admin/console/')

    assert response.headers['cache-control'] == 'no-cache'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['x-frame-options'] == 'DENY'
    csp = response.headers['content-security-policy']
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_console_hashed_assets_are_immutable_and_not_counted_as_api_requests() -> None:
    client = _client()
    index = client.get('/admin/console/').text
    match = re.search(r'(?:src|href)="(/admin/console/assets/[^"]+)"', index)
    assert match is not None
    asset_path = match.group(1)

    response = client.get(asset_path)

    assert response.status_code == 200
    assert response.headers['cache-control'] == 'public, max-age=31536000, immutable'
    assert response.headers['x-content-type-options'] == 'nosniff'

    metrics = client.get('/metrics').text
    assert '/admin/console/assets/' not in metrics


def test_console_client_route_falls_back_to_spa_entry() -> None:
    client = _client()
    root = client.get('/admin/console/')
    routed = client.get('/admin/console/runtimes')

    assert routed.status_code == 200
    assert routed.text == root.text
    assert routed.headers['cache-control'] == 'no-cache'


def test_console_routes_are_not_part_of_public_openapi_contract() -> None:
    app = create_gateway_app(settings(), FakeGatewayClients())
    paths = set(app.openapi()['paths'])

    assert not any(path.startswith('/admin/console') for path in paths)


def test_console_root_without_trailing_slash_redirects_to_canonical_path() -> None:
    client = _client()
    response = client.get('/admin/console', follow_redirects=False)

    assert response.status_code == 307
    assert response.headers['location'] == '/admin/console/'
