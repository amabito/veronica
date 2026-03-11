# tests/test_api_openapi.py
"""Tests for OpenAPI spec auto-generation and /docs accessibility."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def openapi_spec(client: TestClient) -> dict:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, f"openapi.json failed: {resp.text}"
    return resp.json()


class TestOpenAPIEndpoints:
    def test_openapi_json_returns_200(self, client: TestClient) -> None:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200

    def test_docs_returns_200(self, client: TestClient) -> None:
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_redoc_returns_200(self, client: TestClient) -> None:
        resp = client.get("/redoc")
        assert resp.status_code == 200

    def test_openapi_json_is_valid_json(self, client: TestClient) -> None:
        import json

        resp = client.get("/openapi.json")
        data = json.loads(resp.text)
        assert isinstance(data, dict)


class TestOpenAPIMetadata:
    def test_title(self, openapi_spec: dict) -> None:
        assert openapi_spec["info"]["title"] == "VERONICA Control Plane API"

    def test_version(self, openapi_spec: dict) -> None:
        assert openapi_spec["info"]["version"] == "0.8.0"

    def test_description_present(self, openapi_spec: dict) -> None:
        desc = openapi_spec["info"].get("description", "")
        assert len(desc) > 0

    def test_openapi_version(self, openapi_spec: dict) -> None:
        # FastAPI generates OpenAPI 3.1.x
        assert openapi_spec["openapi"].startswith("3.")


class TestOpenAPITags:
    def test_health_tag_present(self, openapi_spec: dict) -> None:
        tag_names = {t["name"] for t in openapi_spec.get("tags", [])}
        assert "health" in tag_names

    def test_policies_tag_present(self, openapi_spec: dict) -> None:
        tag_names = {t["name"] for t in openapi_spec.get("tags", [])}
        assert "policies" in tag_names

    def test_simulate_tag_present(self, openapi_spec: dict) -> None:
        tag_names = {t["name"] for t in openapi_spec.get("tags", [])}
        assert "simulate" in tag_names

    def test_events_tag_present(self, openapi_spec: dict) -> None:
        tag_names = {t["name"] for t in openapi_spec.get("tags", [])}
        assert "events" in tag_names


class TestOpenAPIPaths:
    def test_health_path_present(self, openapi_spec: dict) -> None:
        assert "/health" in openapi_spec["paths"]

    def test_events_path_present(self, openapi_spec: dict) -> None:
        assert "/events" in openapi_spec["paths"]

    def test_simulate_path_present(self, openapi_spec: dict) -> None:
        assert "/simulate" in openapi_spec["paths"]

    def test_policies_path_present(self, openapi_spec: dict) -> None:
        assert "/policies" in openapi_spec["paths"]

    def test_policies_by_id_path_present(self, openapi_spec: dict) -> None:
        assert "/policies/{chain_id}" in openapi_spec["paths"]

    def test_health_has_get(self, openapi_spec: dict) -> None:
        assert "get" in openapi_spec["paths"]["/health"]

    def test_events_has_get(self, openapi_spec: dict) -> None:
        assert "get" in openapi_spec["paths"]["/events"]

    def test_simulate_has_post(self, openapi_spec: dict) -> None:
        assert "post" in openapi_spec["paths"]["/simulate"]


class TestOpenAPISchemas:
    def test_components_schemas_present(self, openapi_spec: dict) -> None:
        assert "components" in openapi_spec
        assert "schemas" in openapi_spec["components"]

    def test_policy_response_schema(self, openapi_spec: dict) -> None:
        schemas = openapi_spec["components"]["schemas"]
        assert "PolicyResponse" in schemas

    def test_simulate_request_schema(self, openapi_spec: dict) -> None:
        schemas = openapi_spec["components"]["schemas"]
        assert "SimulateRequest" in schemas

    def test_simulate_response_schema(self, openapi_spec: dict) -> None:
        schemas = openapi_spec["components"]["schemas"]
        assert "SimulateResponse" in schemas

    def test_events_response_schema(self, openapi_spec: dict) -> None:
        schemas = openapi_spec["components"]["schemas"]
        assert "EventsResponse" in schemas


class TestOpenAPIAuthExemption:
    def test_openapi_json_exempt_from_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_API_KEY", "secret")
        app = create_app()
        c = TestClient(app)
        resp = c.get("/openapi.json")
        assert resp.status_code == 200

    def test_docs_exempt_from_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_API_KEY", "secret")
        app = create_app()
        c = TestClient(app)
        resp = c.get("/docs")
        assert resp.status_code == 200
