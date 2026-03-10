# src/veronica/schemas/events.py
"""Unified CP event schema for VERONICA OS.

These dataclasses are the stable API surface for the control-plane
event pipeline. They are distinct from veronica-core's SafetyEvent
(which is the kernel-level record). CP schemas add audit tracing fields
(policy_hash, audit_id) required for compliance.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


_VALID_DECISIONS = frozenset({"allow", "halt", "degrade", "retry", "quarantine", "queue", "unknown"})
_VALID_VERDICTS = frozenset({"ALLOW", "HALT", "DEGRADE"})


@dataclass(frozen=True)
class StepOutcome:
    """CP-level post-execution record.

    Augments the OS StepOutcome with audit tracing fields required by
    the control plane: policy_hash and audit_id.

    Fields
    ------
    step_id        : unique identifier for this step
    chain_id       : chain/session scope
    operation_name : human-readable label (model name, tool name, etc.)
    decision       : enforcement verdict
    cost_usd       : actual spend for this step
    tokens         : total tokens consumed (in + out)
    duration_ms    : wall-clock elapsed time
    policy_hash    : SHA-256 of the serialized PolicyConfig (hex)
    audit_id       : UUID4 per decision for log correlation
    timestamp      : Unix epoch seconds (float) when recorded
    """

    step_id: str
    chain_id: str
    operation_name: str
    decision: Literal["allow", "halt", "degrade", "retry", "quarantine", "queue", "unknown"]
    cost_usd: float
    tokens: int
    duration_ms: float
    policy_hash: str
    audit_id: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict for storage or wire transfer."""
        return {
            "step_id": self.step_id,
            "chain_id": self.chain_id,
            "operation_name": self.operation_name,
            "decision": self.decision,
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "duration_ms": self.duration_ms,
            "policy_hash": self.policy_hash,
            "audit_id": self.audit_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepOutcome":
        """Deserialize from plain dict.

        Raises ValueError if decision is not in the valid set.
        """
        _ts = data.get("timestamp")
        decision = str(data["decision"])
        if decision not in _VALID_DECISIONS:
            raise ValueError(f"Invalid decision: {decision!r}")
        cost_usd = float(data["cost_usd"])
        duration_ms = float(data["duration_ms"])
        timestamp = float(_ts) if _ts is not None else time.time()
        if not math.isfinite(cost_usd):
            raise ValueError(f"Non-finite cost_usd: {cost_usd!r}")
        if not math.isfinite(duration_ms):
            raise ValueError(f"Non-finite duration_ms: {duration_ms!r}")
        if not math.isfinite(timestamp):
            raise ValueError(f"Non-finite timestamp: {timestamp!r}")
        return cls(
            step_id=str(data["step_id"]),
            chain_id=str(data["chain_id"]),
            operation_name=str(data["operation_name"]),
            decision=decision,
            cost_usd=cost_usd,
            tokens=int(data["tokens"]),
            duration_ms=duration_ms,
            policy_hash=str(data["policy_hash"]),
            audit_id=str(data["audit_id"]),
            timestamp=timestamp,
        )


@dataclass(frozen=True)
class PolicyDecision:
    """CP-level audit record for one policy enforcement decision.

    Every time the OS produces a PolicyConfig, a PolicyDecision is
    emitted so the audit trail records which rule matched, what verdict
    was reached, and which policy version was active.

    Fields
    ------
    decision_id  : UUID4 unique to this decision instance
    policy_id    : logical identifier for the policy (e.g. chain_id)
    policy_hash  : SHA-256 of the serialized PolicyConfig (hex)
    rule_matched : name of the rule or stage that triggered the verdict
    verdict      : ALLOW / HALT / DEGRADE
    reason       : human-readable explanation
    context      : arbitrary key-value bag for extra audit data
    timestamp    : Unix epoch seconds (float) when decision was made
    """

    decision_id: str
    policy_id: str
    policy_hash: str
    rule_matched: str
    verdict: Literal["ALLOW", "HALT", "DEGRADE"]
    reason: str
    context: Mapping[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict for storage or wire transfer."""
        return {
            "decision_id": self.decision_id,
            "policy_id": self.policy_id,
            "policy_hash": self.policy_hash,
            "rule_matched": self.rule_matched,
            "verdict": self.verdict,
            "reason": self.reason,
            "context": dict(self.context),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PolicyDecision":
        """Deserialize from plain dict.

        Raises ValueError if verdict is not in the valid set.
        """
        _ts = data.get("timestamp")
        verdict = str(data["verdict"])
        if verdict not in _VALID_VERDICTS:
            raise ValueError(f"Invalid verdict: {verdict!r}")
        timestamp = float(_ts) if _ts is not None else time.time()
        if not math.isfinite(timestamp):
            raise ValueError(f"Non-finite timestamp: {timestamp!r}")
        return cls(
            decision_id=str(data["decision_id"]),
            policy_id=str(data["policy_id"]),
            policy_hash=str(data["policy_hash"]),
            rule_matched=str(data["rule_matched"]),
            verdict=verdict,
            reason=str(data["reason"]),
            context=dict(data.get("context", {})),
            timestamp=timestamp,
        )
