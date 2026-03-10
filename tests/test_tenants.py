from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("VERONICA_AUTH_DISABLED", "1")

from veronica.api.app import create_app  # noqa: E402
from veronica.tenants.models import TenantNode  # noqa: E402
from veronica.tenants.registry import TenantRegistry  # noqa: E402
from veronica.tenants.resolver import PolicyResolver  # noqa: E402
from veronica.types import PolicyConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> TenantRegistry:
    return TenantRegistry()


@pytest.fixture()
def resolver() -> PolicyResolver:
    return PolicyResolver()


@pytest.fixture()
def base_policy() -> PolicyConfig:
    return PolicyConfig(
        chain_id="root",
        ceiling_usd=10.0,
        on_exceed="halt",
        issued_at=0.0,
    )


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# TenantRegistry -- basic CRUD
# ---------------------------------------------------------------------------


def test_registry_register_and_get(registry: TenantRegistry) -> None:
    node = TenantNode(id="org1", name="Org One")
    registry.register(node)
    result = registry.get("org1")
    assert result is not None
    assert result.name == "Org One"


def test_registry_get_missing_returns_none(registry: TenantRegistry) -> None:
    assert registry.get("nonexistent") is None


def test_registry_list_all_empty(registry: TenantRegistry) -> None:
    assert registry.list_all() == []


def test_registry_list_all_populated(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="a", name="A"))
    registry.register(TenantNode(id="b", name="B"))
    ids = {t.id for t in registry.list_all()}
    assert ids == {"a", "b"}


def test_registry_update_name(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="t1", name="Old"))
    registry.update("t1", {"name": "New"})
    assert registry.get("t1").name == "New"


def test_registry_update_policy_overrides(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="t1", name="T"))
    registry.update("t1", {"policy_overrides": {"ceiling_usd": 50.0}})
    assert registry.get("t1").policy_overrides["ceiling_usd"] == 50.0


def test_registry_delete(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="t1", name="T"))
    registry.delete("t1")
    assert registry.get("t1") is None


def test_registry_get_children(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="parent", name="Parent"))
    registry.register(TenantNode(id="child1", name="C1", parent_id="parent"))
    registry.register(TenantNode(id="child2", name="C2", parent_id="parent"))
    children = {c.id for c in registry.get_children("parent")}
    assert children == {"child1", "child2"}


def test_registry_get_ancestors_single_level(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="root", name="Root"))
    registry.register(TenantNode(id="child", name="Child", parent_id="root"))
    ancestors = registry.get_ancestors("child")
    assert [a.id for a in ancestors] == ["root"]


def test_registry_get_ancestors_multi_level(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="org", name="Org"))
    registry.register(TenantNode(id="team", name="Team", parent_id="org"))
    registry.register(TenantNode(id="chain", name="Chain", parent_id="team"))
    ancestors = registry.get_ancestors("chain")
    assert [a.id for a in ancestors] == ["org", "team"]


def test_registry_get_ancestors_no_parent(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="lone", name="Lone"))
    assert registry.get_ancestors("lone") == []


# ---------------------------------------------------------------------------
# TenantRegistry -- error cases
# ---------------------------------------------------------------------------


def test_registry_register_duplicate_raises(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="dup", name="A"))
    with pytest.raises(ValueError, match="already exists"):
        registry.register(TenantNode(id="dup", name="B"))


def test_registry_register_missing_parent_raises(registry: TenantRegistry) -> None:
    with pytest.raises(ValueError, match="Parent tenant"):
        registry.register(TenantNode(id="child", name="C", parent_id="ghost"))


def test_registry_delete_with_children_raises(registry: TenantRegistry) -> None:
    registry.register(TenantNode(id="parent", name="P"))
    registry.register(TenantNode(id="child", name="C", parent_id="parent"))
    with pytest.raises(ValueError, match="has children"):
        registry.delete("parent")


def test_registry_delete_missing_raises(registry: TenantRegistry) -> None:
    with pytest.raises(KeyError):
        registry.delete("gone")


def test_registry_update_missing_raises(registry: TenantRegistry) -> None:
    with pytest.raises(KeyError):
        registry.update("ghost", {"name": "X"})


# ---------------------------------------------------------------------------
# PolicyResolver
# ---------------------------------------------------------------------------


def test_resolver_no_overrides(
    resolver: PolicyResolver, registry: TenantRegistry, base_policy: PolicyConfig
) -> None:
    registry.register(TenantNode(id="t", name="T"))
    result = resolver.resolve("t", registry, base_policy)
    assert result.ceiling_usd == base_policy.ceiling_usd


def test_resolver_single_tenant_override(
    resolver: PolicyResolver, registry: TenantRegistry, base_policy: PolicyConfig
) -> None:
    registry.register(TenantNode(id="t", name="T", policy_overrides={"ceiling_usd": 99.0}))
    result = resolver.resolve("t", registry, base_policy)
    assert result.ceiling_usd == 99.0


def test_resolver_inheritance_child_wins(
    resolver: PolicyResolver, registry: TenantRegistry, base_policy: PolicyConfig
) -> None:
    registry.register(TenantNode(id="org", name="Org", policy_overrides={"ceiling_usd": 20.0}))
    registry.register(
        TenantNode(id="team", name="Team", parent_id="org", policy_overrides={"ceiling_usd": 30.0})
    )
    result = resolver.resolve("team", registry, base_policy)
    assert result.ceiling_usd == 30.0


def test_resolver_inheritance_parent_propagates(
    resolver: PolicyResolver, registry: TenantRegistry, base_policy: PolicyConfig
) -> None:
    registry.register(TenantNode(id="org", name="Org", policy_overrides={"ceiling_usd": 50.0}))
    registry.register(TenantNode(id="team", name="Team", parent_id="org"))
    result = resolver.resolve("team", registry, base_policy)
    assert result.ceiling_usd == 50.0


def test_resolver_unknown_tenant_returns_base(
    resolver: PolicyResolver, registry: TenantRegistry, base_policy: PolicyConfig
) -> None:
    result = resolver.resolve("ghost", registry, base_policy)
    assert result is base_policy


def test_resolver_ignores_unknown_override_fields(
    resolver: PolicyResolver, registry: TenantRegistry, base_policy: PolicyConfig
) -> None:
    registry.register(
        TenantNode(id="t", name="T", policy_overrides={"not_a_real_field": 999, "ceiling_usd": 5.0})
    )
    result = resolver.resolve("t", registry, base_policy)
    assert result.ceiling_usd == 5.0
    assert not hasattr(result, "not_a_real_field")


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


def test_api_list_tenants_empty(client) -> None:
    resp = client.get("/tenants")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_create_tenant(client) -> None:
    resp = client.post("/tenants", json={"id": "org1", "name": "Org One"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "org1"
    assert data["name"] == "Org One"
    assert data["parent_id"] is None


def test_api_list_tenants_after_create(client) -> None:
    client.post("/tenants", json={"id": "a", "name": "A"})
    client.post("/tenants", json={"id": "b", "name": "B"})
    resp = client.get("/tenants")
    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()}
    assert "a" in ids and "b" in ids


def test_api_get_tenant(client) -> None:
    client.post("/tenants", json={"id": "t1", "name": "T1"})
    resp = client.get("/tenants/t1")
    assert resp.status_code == 200
    assert resp.json()["name"] == "T1"


def test_api_get_tenant_not_found(client) -> None:
    resp = client.get("/tenants/ghost")
    assert resp.status_code == 404


def test_api_create_tenant_with_parent(client) -> None:
    client.post("/tenants", json={"id": "org", "name": "Org"})
    resp = client.post("/tenants", json={"id": "team", "name": "Team", "parent_id": "org"})
    assert resp.status_code == 201
    assert resp.json()["parent_id"] == "org"


def test_api_create_tenant_duplicate_returns_422(client) -> None:
    client.post("/tenants", json={"id": "dup", "name": "D"})
    resp = client.post("/tenants", json={"id": "dup", "name": "D2"})
    assert resp.status_code == 422


def test_api_update_tenant(client) -> None:
    client.post("/tenants", json={"id": "t1", "name": "Old"})
    resp = client.put("/tenants/t1", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


def test_api_update_tenant_not_found(client) -> None:
    resp = client.put("/tenants/ghost", json={"name": "X"})
    assert resp.status_code == 404


def test_api_delete_tenant(client) -> None:
    client.post("/tenants", json={"id": "t1", "name": "T1"})
    resp = client.delete("/tenants/t1")
    assert resp.status_code == 204
    assert client.get("/tenants/t1").status_code == 404


def test_api_delete_tenant_not_found(client) -> None:
    resp = client.delete("/tenants/ghost")
    assert resp.status_code == 404


def test_api_delete_tenant_with_children_returns_409(client) -> None:
    client.post("/tenants", json={"id": "parent", "name": "P"})
    client.post("/tenants", json={"id": "child", "name": "C", "parent_id": "parent"})
    resp = client.delete("/tenants/parent")
    assert resp.status_code == 409


def test_api_effective_policy_no_overrides(client) -> None:
    client.post("/tenants", json={"id": "t1", "name": "T1"})
    resp = client.get("/tenants/t1/effective-policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == "t1"
    assert data["ceiling_usd"] == 10.0
    assert data["on_exceed"] == "halt"


def test_api_effective_policy_with_overrides(client) -> None:
    client.post(
        "/tenants",
        json={"id": "t1", "name": "T1", "policy_overrides": {"ceiling_usd": 99.0}},
    )
    resp = client.get("/tenants/t1/effective-policy")
    assert resp.status_code == 200
    assert resp.json()["ceiling_usd"] == 99.0


def test_api_effective_policy_inheritance(client) -> None:
    client.post(
        "/tenants",
        json={"id": "org", "name": "Org", "policy_overrides": {"ceiling_usd": 50.0}},
    )
    client.post("/tenants", json={"id": "team", "name": "Team", "parent_id": "org"})
    resp = client.get("/tenants/team/effective-policy")
    assert resp.status_code == 200
    assert resp.json()["ceiling_usd"] == 50.0


def test_api_effective_policy_not_found(client) -> None:
    resp = client.get("/tenants/ghost/effective-policy")
    assert resp.status_code == 404


def test_tenant_node_created_at_is_set() -> None:
    before = time.time()
    node = TenantNode(id="x", name="X")
    after = time.time()
    assert before <= node.created_at <= after


def test_api_create_tenant_with_policy_overrides(client) -> None:
    resp = client.post(
        "/tenants",
        json={"id": "t1", "name": "T1", "policy_overrides": {"ceiling_usd": 25.0}},
    )
    assert resp.status_code == 201
    assert resp.json()["policy_overrides"]["ceiling_usd"] == 25.0


def test_api_update_policy_overrides(client) -> None:
    client.post("/tenants", json={"id": "t1", "name": "T"})
    resp = client.put("/tenants/t1", json={"policy_overrides": {"ceiling_usd": 77.0}})
    assert resp.status_code == 200
    assert resp.json()["policy_overrides"]["ceiling_usd"] == 77.0
