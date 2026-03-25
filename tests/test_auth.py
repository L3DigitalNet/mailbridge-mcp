"""Tests for BearerAuthMiddleware — token validation, /health bypass, edge cases."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mailbridge_mcp.auth import BearerAuthMiddleware

API_KEY = "test-secret-key-abc123"


async def echo_handler(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


async def health_handler(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@pytest.fixture
def client() -> TestClient:
    app = Starlette(
        routes=[
            Route("/test", echo_handler),
            Route("/health", health_handler),
        ],
    )
    app.add_middleware(BearerAuthMiddleware, api_key=API_KEY)
    return TestClient(app)


def test_valid_bearer_token(client: TestClient):
    resp = client.get("/test", headers={"Authorization": f"Bearer {API_KEY}"})
    assert resp.status_code == 200
    assert resp.text == "OK"


def test_missing_authorization_header(client: TestClient):
    resp = client.get("/test")
    assert resp.status_code == 401
    assert resp.json()["error"] == "Unauthorized"


def test_wrong_token(client: TestClient):
    resp = client.get("/test", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401


def test_malformed_header_no_bearer_prefix(client: TestClient):
    resp = client.get("/test", headers={"Authorization": "Basic abc123"})
    assert resp.status_code == 401


def test_empty_bearer_token(client: TestClient):
    resp = client.get("/test", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_health_endpoint_bypasses_auth(client: TestClient):
    """The /health endpoint must be accessible without any auth token."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_bypass_with_wrong_token(client: TestClient):
    """/health should work even if a bad token is sent."""
    resp = client.get("/health", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 200


def test_well_known_paths_bypass_auth(client: TestClient):
    """OAuth discovery paths must not return 401 — MCP clients probe these."""
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code != 401  # 404 is fine, 401 triggers OAuth loop

    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code != 401
