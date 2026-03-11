# tests/test_signed_bundles.py
"""Tests for HMAC-SHA256 signed policy bundles."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from veronica.distribution.policy_distributor import (
    PolicyDistributor,
    verify_signature,
)
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


class TestSignedBundles:
    def test_no_signature_when_key_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VERONICA_POLICY_SIGNING_KEY", raising=False)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is None

    def test_signature_present_when_key_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", "test-secret-key")
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is not None
        assert len(bundle.policy_signature) == 64  # SHA-256 hex = 64 chars

    def test_signature_is_valid_hmac(self, monkeypatch: pytest.MonkeyPatch) -> None:
        key = "my-signing-key-abc123"
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", key)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())

        expected = hmac.new(
            key.encode(), bundle.policy_hash.encode(), hashlib.sha256
        ).hexdigest()
        assert bundle.policy_signature == expected

    def test_verify_signature_passes_with_correct_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key = "correct-key"
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", key)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert verify_signature(bundle, key.encode()) is True

    def test_verify_signature_fails_with_wrong_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", "original-key")
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert verify_signature(bundle, b"wrong-key") is False

    def test_verify_signature_raises_when_no_signature(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VERONICA_POLICY_SIGNING_KEY", raising=False)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        with pytest.raises(ValueError, match="no policy_signature"):
            verify_signature(bundle, b"some-key")

    def test_signature_differs_per_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", "shared-key")
        dist = PolicyDistributor()
        bundle_a = dist.distribute(_policy(chain_id="chain-a", ceiling_usd=1.0))
        bundle_b = dist.distribute(_policy(chain_id="chain-b", ceiling_usd=2.0))
        assert bundle_a.policy_signature != bundle_b.policy_signature

    def test_signature_stable_for_same_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", "stable-key")
        dist = PolicyDistributor()
        policy = _policy()
        bundle1 = dist.distribute(policy)
        bundle2 = dist.distribute(policy)
        # Same policy_hash -> same signature
        assert bundle1.policy_hash == bundle2.policy_hash
        assert bundle1.policy_signature == bundle2.policy_signature

    def test_distribute_many_signs_all_bundles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key = "batch-key"
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", key)
        dist = PolicyDistributor()
        policies = [
            _policy(chain_id=f"chain-{i}", ceiling_usd=float(i + 1)) for i in range(3)
        ]
        bundles = dist.distribute_many(policies)
        for bundle in bundles:
            assert bundle.policy_signature is not None
            assert verify_signature(bundle, key.encode()) is True


class TestAdversarialSignedBundles:
    def test_empty_signing_key_treated_as_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", "")
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is None

    def test_verify_with_tampered_policy_hash_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace

        key = "tamper-test-key"
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", key)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())

        tampered = replace(bundle, policy_hash="a" * 64)
        assert verify_signature(tampered, key.encode()) is False

    def test_hmac_constant_time_comparison(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", "ct-key")
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        # hmac.compare_digest used -- returns bool, not raises
        result = verify_signature(bundle, b"wrong")
        assert isinstance(result, bool)
        assert result is False
