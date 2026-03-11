# tests/test_policy_distributor.py
"""Tests for PolicyDistributor -- PolicyConfig -> ExecutionConfig bridge."""

from __future__ import annotations

import threading
import time

import pytest

from veronica.distribution import PolicyBundle, PolicyDistributor, PolicyValidationError
from veronica.types import PolicyConfig


def _policy(**kwargs) -> PolicyConfig:
    defaults = dict(
        chain_id="test-chain",
        ceiling_usd=1.0,
        on_exceed="halt",
        issued_at=1_000_000.0,
    )
    defaults.update(kwargs)
    return PolicyConfig(**defaults)


class TestPolicyValidation:
    def test_valid_policy_passes(self) -> None:
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert isinstance(bundle, PolicyBundle)

    def test_negative_ceiling_usd_raises(self) -> None:
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="ceiling_usd"):
            dist.distribute(_policy(ceiling_usd=-0.01))

    def test_zero_ceiling_usd_is_valid(self) -> None:
        """Zero USD ceiling is allowed (e.g., org policy denied chain)."""
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy(ceiling_usd=0.0))
        assert bundle.exec_config.max_cost_usd == 0.0

    def test_invalid_on_exceed_raises(self) -> None:
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="on_exceed"):
            dist.distribute(_policy(on_exceed="explode"))  # type: ignore[arg-type]

    def test_all_on_exceed_values_accepted(self) -> None:
        dist = PolicyDistributor()
        for value in ("halt", "degrade", "queue"):
            bundle = dist.distribute(_policy(on_exceed=value))  # type: ignore[arg-type]
            assert bundle.policy.on_exceed == value

    def test_strict_mode_rejects_none_fields(self) -> None:
        dist = PolicyDistributor(strict=True)
        with pytest.raises(PolicyValidationError, match="strict mode"):
            dist.distribute(_policy(ceiling_steps=None))

    def test_strict_mode_passes_with_all_fields(self) -> None:
        dist = PolicyDistributor(strict=True)
        bundle = dist.distribute(
            _policy(
                ceiling_steps=50,
                ceiling_tokens_out=10_000,
                timeout_ms=30_000,
            )
        )
        assert bundle.exec_config.max_steps == 50

    def test_non_strict_mode_accepts_none_fields(self) -> None:
        dist = PolicyDistributor(strict=False)
        bundle = dist.distribute(
            _policy(
                ceiling_steps=None,
                ceiling_tokens_out=None,
                timeout_ms=None,
            )
        )
        assert bundle.policy.ceiling_steps is None


class TestPolicyBundle:
    def test_bundle_carries_original_policy(self) -> None:
        dist = PolicyDistributor()
        policy = _policy(chain_id="my-chain", ceiling_usd=5.0)
        bundle = dist.distribute(policy)
        assert bundle.policy is policy

    def test_bundle_has_sha256_hash(self) -> None:
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert len(bundle.policy_hash) == 64
        assert all(c in "0123456789abcdef" for c in bundle.policy_hash)

    def test_identical_policies_same_hash(self) -> None:
        dist = PolicyDistributor()
        p = _policy(ceiling_usd=1.0, issued_at=1_000_000.0)
        b1 = dist.distribute(p)
        b2 = dist.distribute(p)
        assert b1.policy_hash == b2.policy_hash

    def test_different_policies_different_hash(self) -> None:
        dist = PolicyDistributor()
        b1 = dist.distribute(_policy(ceiling_usd=1.0))
        b2 = dist.distribute(_policy(ceiling_usd=2.0))
        assert b1.policy_hash != b2.policy_hash

    def test_version_monotonically_increases(self) -> None:
        dist = PolicyDistributor()
        versions = [dist.distribute(_policy()).version for _ in range(5)]
        assert versions == sorted(versions)
        assert versions[0] == 1
        assert versions[-1] == 5

    def test_distributed_at_is_recent(self) -> None:
        dist = PolicyDistributor()
        before = time.time()
        bundle = dist.distribute(_policy())
        after = time.time()
        assert before <= bundle.distributed_at <= after

    def test_exec_config_reflects_policy(self) -> None:
        dist = PolicyDistributor()
        bundle = dist.distribute(
            _policy(
                ceiling_usd=2.5,
                ceiling_steps=20,
                timeout_ms=15_000,
            )
        )
        assert bundle.exec_config.max_cost_usd == 2.5
        assert bundle.exec_config.max_steps == 20
        assert bundle.exec_config.timeout_ms == 15_000


class TestDistributeMany:
    def test_all_valid_returns_all_bundles(self) -> None:
        dist = PolicyDistributor()
        policies = [_policy(chain_id=f"c{i}") for i in range(3)]
        bundles = dist.distribute_many(policies)
        assert len(bundles) == 3
        assert [b.policy.chain_id for b in bundles] == ["c0", "c1", "c2"]

    def test_partial_failure_raises_with_all_errors(self) -> None:
        dist = PolicyDistributor()
        policies = [
            _policy(chain_id="ok"),
            _policy(chain_id="bad", ceiling_usd=-1.0),
            _policy(chain_id="ok2"),
            _policy(chain_id="bad2", on_exceed="explode"),  # type: ignore[arg-type]
        ]
        with pytest.raises(PolicyValidationError) as exc_info:
            dist.distribute_many(policies)
        msg = str(exc_info.value)
        assert "2 policy validation error" in msg
        assert "chain_id='bad'" in msg
        assert "chain_id='bad2'" in msg

    def test_empty_sequence_returns_empty(self) -> None:
        dist = PolicyDistributor()
        result = dist.distribute_many([])
        assert result == []


class TestThreadSafety:
    def test_concurrent_distribute_unique_versions(self) -> None:
        """All concurrent distribute() calls get unique, monotonic versions."""
        dist = PolicyDistributor()
        results: list[PolicyBundle] = []
        lock = threading.Lock()

        def worker() -> None:
            bundle = dist.distribute(_policy())
            with lock:
                results.append(bundle)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        versions = sorted(b.version for b in results)
        assert len(set(versions)) == 20  # all unique
        assert versions == list(range(1, 21))  # contiguous 1..20

    def test_concurrent_distribute_many_no_race(self) -> None:
        """distribute_many from multiple threads does not corrupt version counter."""
        dist = PolicyDistributor()
        all_versions: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            bundles = dist.distribute_many([_policy(chain_id="c")] * 5)
            with lock:
                all_versions.extend(b.version for b in bundles)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(all_versions) == 20
        assert len(set(all_versions)) == 20  # all unique
