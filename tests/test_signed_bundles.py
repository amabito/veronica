# tests/test_signed_bundles.py
"""Tests for HMAC-SHA256 signed policy bundles."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from veronica.distribution.policy_distributor import (
    PolicyDistributor,
    PolicyValidationError,
    verify_signature,
)
from veronica.types import PolicyConfig

# All test keys must be >= 32 chars (minimum enforced by PolicyDistributor)
_KEY_A = "test-secret-key-0123456789abcdef"
_KEY_B = "my-signing-key-abc1234567890abcde"
_KEY_C = "correct-key-0123456789abcdef0123"
_KEY_ORIG = "original-key-0123456789abcdef012"
_KEY_SHARED = "shared-key-0123456789abcdef01234"
_KEY_STABLE = "stable-key-0123456789abcdef01234"
_KEY_BATCH = "batch-key-0123456789abcdef012345"
_KEY_TAMPER = "tamper-test-key-0123456789abcdef"
_KEY_CT = "ct-key-0123456789abcdef0123456789ab"


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
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", _KEY_A)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is not None
        assert len(bundle.policy_signature) == 64  # SHA-256 hex = 64 chars

    def test_signature_is_valid_hmac(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", _KEY_B)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())

        expected = hmac.new(
            _KEY_B.encode(), bundle.policy_hash.encode(), hashlib.sha256
        ).hexdigest()
        assert bundle.policy_signature == expected

    def test_verify_signature_passes_with_correct_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", _KEY_C)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert verify_signature(bundle, _KEY_C.encode()) is True

    def test_verify_signature_fails_with_wrong_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", _KEY_ORIG)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert verify_signature(bundle, b"wrong-key-that-does-not-match-x") is False

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
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", _KEY_SHARED)
        dist = PolicyDistributor()
        bundle_a = dist.distribute(_policy(chain_id="chain-a", ceiling_usd=1.0))
        bundle_b = dist.distribute(_policy(chain_id="chain-b", ceiling_usd=2.0))
        assert bundle_a.policy_signature != bundle_b.policy_signature

    def test_signature_stable_for_same_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", _KEY_STABLE)
        dist = PolicyDistributor()
        policy = _policy()
        bundle1 = dist.distribute(policy)
        bundle2 = dist.distribute(policy)
        assert bundle1.policy_hash == bundle2.policy_hash
        assert bundle1.policy_signature == bundle2.policy_signature

    def test_distribute_many_signs_all_bundles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", _KEY_BATCH)
        dist = PolicyDistributor()
        policies = [
            _policy(chain_id=f"chain-{i}", ceiling_usd=float(i + 1)) for i in range(3)
        ]
        bundles = dist.distribute_many(policies)
        for bundle in bundles:
            assert bundle.policy_signature is not None
            assert verify_signature(bundle, _KEY_BATCH.encode()) is True


class TestAdversarialSignedBundles:
    def test_empty_signing_key_treated_as_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", "")
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is None

    def test_short_signing_key_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keys shorter than 32 chars must be rejected at init."""
        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", "too-short")
        with pytest.raises(PolicyValidationError, match="at least 32 characters"):
            PolicyDistributor()

    def test_verify_with_tampered_policy_hash_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace

        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", _KEY_TAMPER)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())

        tampered = replace(bundle, policy_hash="a" * 64)
        assert verify_signature(tampered, _KEY_TAMPER.encode()) is False

    def test_hmac_constant_time_comparison(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        monkeypatch.setenv("VERONICA_POLICY_SIGNING_KEY", _KEY_CT)
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())

        # Verify hmac.compare_digest is actually called (not plain ==)
        with patch(
            "veronica.distribution.policy_distributor.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as mock_cd:
            result = verify_signature(bundle, b"wrong")
            assert mock_cd.called, "verify_signature must use hmac.compare_digest"
        assert isinstance(result, bool)
        assert result is False
