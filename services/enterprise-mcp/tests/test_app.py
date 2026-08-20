from __future__ import annotations

import base64
import importlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient


def test_transport_security_allows_compose_and_local_hosts(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setenv("YUXI_MCP_SIGNING_PUBLIC_KEY_B64", base64.b64encode(public_pem).decode())

    app_module = importlib.import_module("enterprise_mcp.app")
    allowed_hosts = app_module.MCP_TRANSPORT_SECURITY.allowed_hosts

    assert "enterprise-mcp:8010" in allowed_hosts
    assert "127.0.0.1:*" in allowed_hosts


def test_gateway_publishes_meeting_and_hr_on_one_app(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setenv("YUXI_MCP_SIGNING_PUBLIC_KEY_B64", base64.b64encode(public_pem).decode())

    app_module = importlib.import_module("enterprise_mcp.app")
    assert "/mcp/meeting" in app_module.app.endpoint_apps
    assert "/mcp/hr" in app_module.app.endpoint_apps

    with TestClient(app_module.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "domains": ["meeting", "hr"]}
