"""Tests for SimplePlanner and PolicyConfig."""

from __future__ import annotations

import pytest

from veronica.planner import PolicyConfig, SimplePlanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(
    halt_count: int = 0,
    fail_count: int = 0,
    max_depth: int = 3,
    node_count: int = 1,
    total_cost_usd: float = 0.1,
) -> dict:
    return {
        "chain_id": "test-chain",
        "aggregates": {
            "node_count": node_count,
            "total_cost_usd": total_cost_usd,
            "max_depth": max_depth,
            "halt_count": halt_count,
            "fail_count": fail_count,
        },
        "nodes": [],
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_create_config_defaults() -> None:
    """PolicyConfig fields are populated with sensible defaults."""
    p = SimplePlanner()
    cfg = p.create_config(estimated_steps=10)

    assert isinstance(cfg, PolicyConfig)
    assert cfg.ceiling_usd == 1.00
    assert cfg.ceiling_tokens_out == 4096
    assert cfg.on_exceed == "halt"
    assert cfg.planner_version == "simple/0.1.0"
    assert cfg.priority == 50
    assert cfg.fallback_model is None
    assert cfg.timeout_ms == 30_000
    assert len(cfg.chain_id) == 36  # UUID4 format
    assert cfg.issued_at > 0.0


def test_create_config_fields() -> None:
    """ceiling_steps = max(estimated_steps * multiplier, estimated_steps + 5)."""
    p = SimplePlanner(default_steps_multiplier=2.0)

    # 10 * 2.0 = 20, max(20, 15) = 20
    cfg = p.create_config(estimated_steps=10)
    assert cfg.ceiling_steps == 20

    # 3 * 2.0 = 6, max(6, 8) = 8
    cfg2 = p.create_config(estimated_steps=3)
    assert cfg2.ceiling_steps == 8


def test_update_rule1_halt_tightening() -> None:
    """halt_count > 0 reduces ceiling by 10%."""
    p = SimplePlanner(base_ceiling_usd=1.00)
    p.update(_snapshot(halt_count=1))
    cfg = p.create_config(estimated_steps=5)

    assert abs(cfg.ceiling_usd - 0.90) < 1e-9


def test_update_rule2_clean_loosening() -> None:
    """No halt or fail increases ceiling by 5%."""
    p = SimplePlanner(base_ceiling_usd=1.00)
    p.update(_snapshot(halt_count=0, fail_count=0))
    cfg = p.create_config(estimated_steps=5)

    assert abs(cfg.ceiling_usd - 1.05) < 1e-9


def test_update_rule1_floor() -> None:
    """Repeated halts do not push ceiling below min_ceiling_usd."""
    p = SimplePlanner(base_ceiling_usd=0.15, min_ceiling_usd=0.10)
    for _ in range(20):
        p.update(_snapshot(halt_count=1))
    cfg = p.create_config(estimated_steps=5)

    assert cfg.ceiling_usd >= 0.10


def test_update_rule2_cap() -> None:
    """Repeated clean runs do not exceed max_ceiling_usd."""
    p = SimplePlanner(base_ceiling_usd=9.50, max_ceiling_usd=10.00)
    for _ in range(20):
        p.update(_snapshot(halt_count=0, fail_count=0))
    cfg = p.create_config(estimated_steps=5)

    assert cfg.ceiling_usd <= 10.00


def test_update_rule3_depth_guard() -> None:
    """max_depth >= 8 forces next config to use on_exceed='halt'."""
    p = SimplePlanner(default_on_exceed="degrade")
    p.update(_snapshot(max_depth=8))
    cfg = p.create_config(estimated_steps=5, priority=50)

    assert cfg.on_exceed == "halt"


def test_update_rule3_priority_override() -> None:
    """priority >= 90 overrides the depth guard (can still use degrade)."""
    p = SimplePlanner(default_on_exceed="degrade")
    p.update(_snapshot(max_depth=8))
    cfg = p.create_config(estimated_steps=5, priority=90)

    assert cfg.on_exceed == "degrade"


def test_policy_config_frozen() -> None:
    """PolicyConfig is immutable — assignment raises FrozenInstanceError."""
    from dataclasses import FrozenInstanceError

    p = SimplePlanner()
    cfg = p.create_config(estimated_steps=5)

    with pytest.raises(FrozenInstanceError):
        cfg.ceiling_usd = 99.0  # type: ignore[misc]


def test_chain_id_unique() -> None:
    """Each create_config() returns a unique chain_id."""
    p = SimplePlanner()
    ids = {p.create_config(estimated_steps=5).chain_id for _ in range(20)}
    assert len(ids) == 20


def test_create_config_custom_params() -> None:
    """Custom base/max/min ceilings are respected."""
    p = SimplePlanner(
        base_ceiling_usd=2.50,
        max_ceiling_usd=5.00,
        min_ceiling_usd=0.50,
        default_steps_multiplier=3.0,
        default_timeout_ms=60_000,
        default_on_exceed="degrade",
        fallback_model="gpt-4o-mini",
    )
    cfg = p.create_config(estimated_steps=10, priority=80)

    assert cfg.ceiling_usd == 2.50
    assert cfg.ceiling_steps == 30  # 10 * 3.0 = 30
    assert cfg.timeout_ms == 60_000
    assert cfg.on_exceed == "degrade"
    assert cfg.fallback_model == "gpt-4o-mini"
    assert cfg.priority == 80


def test_history_bounded() -> None:
    """_history stays bounded at 10 entries after 15 updates."""
    p = SimplePlanner()
    for i in range(15):
        p.update(_snapshot(node_count=i))

    assert len(p._history) == 10


def test_to_exec_config_field_mapping() -> None:
    """to_exec_config maps PolicyConfig fields to ExecutionConfig correctly."""
    from veronica_core.containment import ExecutionConfig

    p = SimplePlanner(base_ceiling_usd=2.50, default_timeout_ms=45_000)
    policy = p.create_config(estimated_steps=8)
    exec_cfg = policy.to_exec_config()

    assert isinstance(exec_cfg, ExecutionConfig)
    assert exec_cfg.max_cost_usd == policy.ceiling_usd
    assert exec_cfg.max_steps == policy.ceiling_steps
    assert exec_cfg.timeout_ms == 45_000


def test_to_exec_config_zero_timeout() -> None:
    """to_exec_config converts falsy timeout_ms to 0 (veronica-core disables timeout)."""
    from veronica_core.containment import ExecutionConfig

    # PolicyConfig.timeout_ms=None (default when not set) should map to timeout_ms=0
    import time, uuid
    policy = PolicyConfig(
        ceiling_usd=1.0,
        ceiling_tokens_out=4096,
        ceiling_steps=20,
        on_exceed="halt",
        chain_id=str(uuid.uuid4()),
        issued_at=time.time(),
        planner_version="simple/0.1.0",
        timeout_ms=None,  # explicitly None
    )
    exec_cfg = policy.to_exec_config()

    assert exec_cfg.timeout_ms == 0
