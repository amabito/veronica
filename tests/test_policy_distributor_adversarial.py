# tests/test_policy_distributor_adversarial.py
"""Adversarial tests for PolicyDistributor -- attacker mindset.

Covers branches NOT tested in test_policy_distributor.py:
  - HMAC signature tampering
  - Key validation edge cases (env var injection)
  - Policy field abuse (NaN/Inf/None)
  - Concurrent version counter integrity under high contention
  - Serialization and boundary conditions
"""

from __future__ import annotations

import math
import threading
from unittest.mock import patch

import pytest

from veronica.distribution import PolicyBundle, PolicyDistributor, PolicyValidationError
from veronica.distribution.policy_distributor import (
    verify_signature,
)
from veronica.types import PolicyConfig


def _policy(**kwargs) -> PolicyConfig:
    defaults = dict(
        chain_id="adv-chain",
        ceiling_usd=1.0,
        on_exceed="halt",
        issued_at=1_000_000.0,
    )
    defaults.update(kwargs)
    return PolicyConfig(**defaults)


def _distributor_with_key(key: str) -> PolicyDistributor:
    """Create a PolicyDistributor with a specific signing key via env var."""
    with patch.dict("os.environ", {"VERONICA_POLICY_SIGNING_KEY": key}):
        return PolicyDistributor()


class TestAdversarialPolicyDistributor:
    # ------------------------------------------------------------------ #
    # 1. HMAC signature tampering                                         #
    # ------------------------------------------------------------------ #

    def test_verify_signature_wrong_key_returns_false(self) -> None:
        """Bundle signed with key-A must fail verification against key-B."""
        key_a = "a" * 32
        key_b = "b" * 32
        dist = _distributor_with_key(key_a)
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is not None
        assert verify_signature(bundle, key_b.encode()) is False

    def test_verify_signature_truncated_sig_returns_false(self) -> None:
        """A signature truncated to half length must fail compare_digest."""
        key = "k" * 32
        dist = _distributor_with_key(key)
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is not None
        truncated = bundle.policy_signature[: len(bundle.policy_signature) // 2]
        tampered = PolicyBundle(
            policy=bundle.policy,
            exec_config=bundle.exec_config,
            policy_hash=bundle.policy_hash,
            distributed_at=bundle.distributed_at,
            version=bundle.version,
            policy_signature=truncated,
        )
        assert verify_signature(tampered, key.encode()) is False

    def test_verify_signature_bit_flip_returns_false(self) -> None:
        """Flipping one character in the signature hex must fail verification."""
        key = "s" * 32
        dist = _distributor_with_key(key)
        bundle = dist.distribute(_policy())
        sig = bundle.policy_signature
        assert sig is not None
        # Flip last hex digit: '0'->'1' or '1'->'0', etc.
        flipped_last = ("1" if sig[-1] == "0" else "0")
        corrupted_sig = sig[:-1] + flipped_last
        tampered = PolicyBundle(
            policy=bundle.policy,
            exec_config=bundle.exec_config,
            policy_hash=bundle.policy_hash,
            distributed_at=bundle.distributed_at,
            version=bundle.version,
            policy_signature=corrupted_sig,
        )
        assert verify_signature(tampered, key.encode()) is False

    def test_verify_signature_none_raises_value_error(self) -> None:
        """Calling verify_signature on a bundle without a signature must raise."""
        dist = PolicyDistributor()  # no signing key
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is None
        with pytest.raises(ValueError, match="no policy_signature"):
            verify_signature(bundle, b"any-key-does-not-matter")

    def test_verify_signature_correct_key_returns_true(self) -> None:
        """Signing key used for distribution must verify successfully."""
        key = "v" * 32
        dist = _distributor_with_key(key)
        bundle = dist.distribute(_policy())
        assert verify_signature(bundle, key.encode()) is True

    def test_no_signing_key_produces_no_signature(self) -> None:
        """Without VERONICA_POLICY_SIGNING_KEY the bundle carries no signature."""
        with patch.dict("os.environ", {}, clear=False):
            # Ensure key is absent
            import os

            os.environ.pop("VERONICA_POLICY_SIGNING_KEY", None)
            dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is None

    # ------------------------------------------------------------------ #
    # 2. Key validation -- env var injection                              #
    # ------------------------------------------------------------------ #

    def test_signing_key_31_chars_raises_on_init(self) -> None:
        """A 31-character key is one byte short of the minimum -- must raise."""
        short_key = "x" * 31
        with pytest.raises(PolicyValidationError, match="at least 32"):
            _distributor_with_key(short_key)

    def test_signing_key_1_char_raises_on_init(self) -> None:
        """A single-character key must raise immediately on init."""
        with pytest.raises(PolicyValidationError, match="at least 32"):
            _distributor_with_key("a")

    def test_signing_key_exactly_32_chars_accepted(self) -> None:
        """A key of exactly 32 characters is the minimum and must be accepted."""
        dist = _distributor_with_key("y" * 32)
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is not None

    def test_signing_key_very_long_accepted(self) -> None:
        """A 4096-character key must be accepted without error."""
        long_key = "z" * 4096
        dist = _distributor_with_key(long_key)
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is not None

    def test_signing_key_non_ascii_bytes_encode_without_crash(self) -> None:
        """A key with characters outside ASCII range must encode and sign."""
        # U+00E9 = é -- still encodable as UTF-8 bytes
        non_ascii_key = "\u00e9" * 32
        dist = _distributor_with_key(non_ascii_key)
        bundle = dist.distribute(_policy())
        assert bundle.policy_signature is not None

    # ------------------------------------------------------------------ #
    # 3. Policy field abuse                                               #
    # ------------------------------------------------------------------ #

    def test_nan_ceiling_usd_raises(self) -> None:
        """NaN ceiling_usd must be rejected before hashing."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="ceiling_usd"):
            dist.distribute(_policy(ceiling_usd=float("nan")))

    def test_positive_inf_ceiling_usd_raises(self) -> None:
        """Positive infinity ceiling_usd must be rejected."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="ceiling_usd"):
            dist.distribute(_policy(ceiling_usd=math.inf))

    def test_negative_inf_ceiling_usd_raises(self) -> None:
        """Negative infinity ceiling_usd must be rejected as non-finite."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="ceiling_usd"):
            dist.distribute(_policy(ceiling_usd=-math.inf))

    def test_nan_ceiling_tokens_out_raises(self) -> None:
        """NaN ceiling_tokens_out must be rejected."""
        dist = PolicyDistributor()
        # ceiling_tokens_out is int | None, so we bypass the type hint
        with pytest.raises(PolicyValidationError, match="ceiling_tokens_out"):
            dist.distribute(_policy(ceiling_tokens_out=float("nan")))  # type: ignore[arg-type]

    def test_nan_timeout_ms_raises(self) -> None:
        """NaN timeout_ms must be rejected."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="timeout_ms"):
            dist.distribute(_policy(timeout_ms=float("nan")))  # type: ignore[arg-type]

    def test_negative_ceiling_steps_raises(self) -> None:
        """Negative ceiling_steps must be rejected."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="ceiling_steps"):
            dist.distribute(_policy(ceiling_steps=-1))

    def test_negative_timeout_ms_raises(self) -> None:
        """Negative timeout_ms must be rejected."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="timeout_ms"):
            dist.distribute(_policy(timeout_ms=-1))

    def test_negative_ceiling_tokens_out_raises(self) -> None:
        """Negative ceiling_tokens_out must be rejected."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="ceiling_tokens_out"):
            dist.distribute(_policy(ceiling_tokens_out=-100))

    def test_priority_as_bool_raises(self) -> None:
        """bool is a subclass of int in Python but must be rejected for priority."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="priority"):
            dist.distribute(_policy(priority=True))  # type: ignore[arg-type]

    def test_priority_as_float_raises(self) -> None:
        """Float priority must be rejected even if it looks like an integer value."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="priority"):
            dist.distribute(_policy(priority=50.0))  # type: ignore[arg-type]

    def test_on_exceed_empty_string_raises(self) -> None:
        """Empty string on_exceed must be rejected with meaningful error."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="on_exceed"):
            dist.distribute(_policy(on_exceed=""))  # type: ignore[arg-type]

    def test_on_exceed_case_sensitive_raises(self) -> None:
        """'HALT' (uppercase) must not pass -- matching is case-sensitive."""
        dist = PolicyDistributor()
        with pytest.raises(PolicyValidationError, match="on_exceed"):
            dist.distribute(_policy(on_exceed="HALT"))  # type: ignore[arg-type]

    def test_extremely_long_chain_id_is_hashed_deterministically(self) -> None:
        """A 10 000-character chain_id must be accepted and produce a stable hash."""
        dist = PolicyDistributor()
        long_id = "x" * 10_000
        b1 = dist.distribute(_policy(chain_id=long_id))
        b2 = dist.distribute(_policy(chain_id=long_id))
        assert b1.policy_hash == b2.policy_hash
        assert len(b1.policy_hash) == 64

    def test_zero_ceiling_steps_is_valid(self) -> None:
        """Zero ceiling_steps (no steps allowed) must pass validation."""
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy(ceiling_steps=0))
        assert bundle.exec_config.max_steps == 0

    def test_zero_timeout_ms_is_valid(self) -> None:
        """Zero timeout_ms must be accepted (no timeout)."""
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy(timeout_ms=0))
        assert bundle.exec_config.timeout_ms == 0

    # ------------------------------------------------------------------ #
    # 4. Concurrent distribution -- race conditions                       #
    # ------------------------------------------------------------------ #

    def test_version_counter_no_gaps_under_high_contention(self) -> None:
        """50 concurrent distribute() calls must produce exactly versions 1..50."""
        dist = PolicyDistributor()
        versions: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            for _ in range(5):
                b = dist.distribute(_policy())
                with lock:
                    versions.append(b.version)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(versions) == 50
        assert sorted(versions) == list(range(1, 51))  # no gaps, no duplicates

    def test_concurrent_distribute_many_mixed_validity_no_corruption(self) -> None:
        """Concurrent distribute_many with all-valid policies must produce unique versions."""
        dist = PolicyDistributor()
        all_versions: list[int] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                bundles = dist.distribute_many([_policy(chain_id=f"c{i}") for i in range(3)])
                with lock:
                    all_versions.extend(b.version for b in bundles)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Unexpected errors: {errors}"
        assert len(all_versions) == 24
        assert len(set(all_versions)) == 24  # all unique, no version aliasing

    # ------------------------------------------------------------------ #
    # 5. distribute_many error aggregation edge cases                     #
    # ------------------------------------------------------------------ #

    def test_distribute_many_all_invalid_reports_all_errors(self) -> None:
        """distribute_many where every policy is invalid must enumerate all errors."""
        dist = PolicyDistributor()
        bad_policies = [
            _policy(chain_id="x1", ceiling_usd=float("nan")),
            _policy(chain_id="x2", on_exceed=""),  # type: ignore[arg-type]
            _policy(chain_id="x3", ceiling_steps=-5),
        ]
        with pytest.raises(PolicyValidationError) as exc_info:
            dist.distribute_many(bad_policies)
        msg = str(exc_info.value)
        assert "3 policy validation error" in msg
        assert "chain_id='x1'" in msg
        assert "chain_id='x2'" in msg
        assert "chain_id='x3'" in msg

    def test_distribute_many_single_invalid_in_last_position_raises(self) -> None:
        """An invalid policy at the last index must still trigger a full error report."""
        dist = PolicyDistributor()
        policies = [
            _policy(chain_id="ok0"),
            _policy(chain_id="ok1"),
            _policy(chain_id="bad-last", ceiling_usd=-9.99),
        ]
        with pytest.raises(PolicyValidationError) as exc_info:
            dist.distribute_many(policies)
        assert "policy[2]" in str(exc_info.value)
        assert "chain_id='bad-last'" in str(exc_info.value)

    # ------------------------------------------------------------------ #
    # 6. Boundary conditions                                              #
    # ------------------------------------------------------------------ #

    def test_max_int_ceiling_steps_accepted(self) -> None:
        """A very large ceiling_steps (sys.maxsize) must not crash validation."""
        import sys

        dist = PolicyDistributor()
        bundle = dist.distribute(_policy(ceiling_steps=sys.maxsize))
        assert bundle.exec_config.max_steps == sys.maxsize

    def test_version_counter_starts_at_one(self) -> None:
        """First distribute() call on a fresh instance must return version == 1."""
        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        assert bundle.version == 1

    def test_strict_mode_reports_all_missing_fields(self) -> None:
        """strict=True must list all three missing optional fields in one error."""
        dist = PolicyDistributor(strict=True)
        with pytest.raises(PolicyValidationError) as exc_info:
            dist.distribute(_policy(ceiling_steps=None, ceiling_tokens_out=None, timeout_ms=None))
        msg = str(exc_info.value)
        assert "ceiling_steps" in msg
        assert "ceiling_tokens_out" in msg
        assert "timeout_ms" in msg

    def test_policy_hash_changes_when_issued_at_changes(self) -> None:
        """Two policies differing only in issued_at must produce different hashes."""
        dist = PolicyDistributor()
        b1 = dist.distribute(_policy(issued_at=1_000_000.0))
        b2 = dist.distribute(_policy(issued_at=1_000_001.0))
        assert b1.policy_hash != b2.policy_hash

    def test_bundle_is_frozen(self) -> None:
        """PolicyBundle is a frozen dataclass -- normal attribute assignment must raise."""
        import dataclasses

        dist = PolicyDistributor()
        bundle = dist.distribute(_policy())
        with pytest.raises(dataclasses.FrozenInstanceError):
            bundle.version = 9999  # type: ignore[misc]
