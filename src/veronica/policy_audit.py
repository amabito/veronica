# src/veronica/policy_audit.py
"""Policy audit utilities: policy_hash and audit_id generation.

policy_hash -- SHA-256 of the canonical serialization of a PolicyConfig.
audit_id    -- UUID4 hex string, unique per decision.

Both are stable, deterministic (policy_hash) or unpredictable (audit_id)
identifiers used to correlate control-plane events across the pipeline.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any


def compute_policy_hash(policy_config: Any) -> str:
    """Return a 64-char hex SHA-256 hash of a PolicyConfig.

    The hash is derived from the canonical JSON serialization of the
    PolicyConfig's public fields, sorted by key for stability.

    Parameters
    ----------
    policy_config : veronica.types.PolicyConfig
        The policy to hash. Must be a dataclass with a __dataclass_fields__
        attribute, or any object with a __dict__.

    Returns
    -------
    str
        64-character lowercase hexadecimal SHA-256 digest.
    """
    canonical = _to_canonical(policy_config)
    serialized = json.dumps(canonical, sort_keys=True, default=_json_default)
    return hashlib.sha256(serialized.encode()).hexdigest()


def new_audit_id() -> str:
    """Generate a new UUID4 audit identifier.

    Returns
    -------
    str
        32-character lowercase hexadecimal UUID4 (no hyphens).
    """
    return uuid.uuid4().hex


def _to_canonical(obj: Any) -> dict[str, Any]:
    """Extract a sorted dict of public fields from a dataclass or dict."""
    if hasattr(obj, "__dataclass_fields__"):
        fields = obj.__dataclass_fields__
        return {
            k: _serialize_value(getattr(obj, k))
            for k in sorted(fields)
            if not k.startswith("_")
        }
    if isinstance(obj, dict):
        return {k: _serialize_value(v) for k, v in sorted(obj.items())}
    return {"value": str(obj)}


def _serialize_value(v: Any) -> Any:
    """Recursively convert a value to a JSON-serializable form."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (frozenset, set)):
        return sorted(str(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [_serialize_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _serialize_value(val) for k, val in sorted(v.items())}
    if hasattr(v, "__dataclass_fields__"):
        return _to_canonical(v)
    return str(v)


def _json_default(obj: Any) -> Any:
    """Fallback serializer for types not handled by _serialize_value."""
    return str(obj)
