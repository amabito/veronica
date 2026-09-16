"""Immutable, provider-neutral contracts. A recommendation is NOT authorization."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def identifier(value: str) -> None:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("expected an opaque ASCII identifier (1..128 characters)")


def number(value: float, *, maximum: float | None = None) -> None:
    if type(value) not in (int, float):
        raise ValueError("expected a finite nonnegative number, not a boolean")
    try:
        valid = math.isfinite(value) and value >= 0
    except (OverflowError, ValueError):
        valid = False
    if not valid or (maximum is not None and value > maximum):
        raise ValueError("number outside permitted range")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise ValueError("nonfinite JSON number")


class Stage(str, Enum):
    RULE = "rule"
    LIGHT = "light"
    FRONTIER = "frontier"


class Outcome(str, Enum):
    RECOMMENDATION = "recommendation"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DecisionRequest:
    request_id: str
    task: str
    choices: tuple[str, ...]
    context_json: str = field(repr=False)

    def __post_init__(self) -> None:
        identifier(self.request_id)
        identifier(self.task)
        if type(self.choices) is not tuple or not 2 <= len(self.choices) <= 32:
            raise ValueError("choices must be an immutable tuple of 2..32 labels")
        for label in self.choices:
            identifier(label)
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("duplicate choice")
        if type(self.context_json) is not str:
            raise ValueError("context_json must be JSON text")
        try:
            if len(self.context_json.encode("utf-8")) > 65536:
                raise ValueError("context exceeds 64 KiB")
            value = json.loads(self.context_json, object_pairs_hook=_object,
                               parse_constant=_nonfinite)
            if type(value) is not dict:
                raise ValueError("context must be a JSON object")
            # Reject excessive nesting and exponent overflow (e.g. 1e999).
            pending = [(value, 0)]
            while pending:
                node, depth = pending.pop()
                if depth > 16:
                    raise ValueError("context exceeds depth limit")
                if isinstance(node, dict):
                    pending.extend((v, depth + 1) for v in node.values())
                elif isinstance(node, list):
                    pending.extend((v, depth + 1) for v in node)
                elif isinstance(node, float) and not math.isfinite(node):
                    raise ValueError("nonfinite JSON number")
            normalized = canonical(value)
            if len(normalized.encode("utf-8")) > 65536:
                raise ValueError("canonical context exceeds 64 KiB")
        except (ValueError, UnicodeError, RecursionError, OverflowError):
            raise ValueError("invalid or oversized context JSON") from None
        object.__setattr__(self, "context_json", normalized)

    def context(self) -> dict[str, Any]:
        """Return a new object; providers cannot mutate the next provider's input."""
        return json.loads(self.context_json)

    @property
    def fingerprint(self) -> str:
        return digest([self.request_id, self.task, self.choices, self.context_json])


@dataclass(frozen=True)
class Assessment:
    choice: str | None
    confidence: float

    def __post_init__(self) -> None:
        if self.choice is not None:
            identifier(self.choice)
        number(self.confidence, maximum=1.0)
        if self.choice is None and self.confidence != 0:
            raise ValueError("abstention must have confidence zero")


@dataclass(frozen=True)
class Provider:
    provider_id: str
    stage: Stage
    assess: Callable[[DecisionRequest], Assessment] = field(repr=False, compare=False)
    implementation_id: str = "v1"
    external: bool = False
    cost_estimate_usd: float = 0.0

    def __post_init__(self) -> None:
        identifier(self.provider_id)
        identifier(self.implementation_id)
        if type(self.stage) is not Stage or not callable(self.assess):
            raise ValueError("invalid provider stage or callable")
        if type(self.external) is not bool:
            raise ValueError("external must be boolean")
        number(self.cost_estimate_usd)
        if self.stage is Stage.RULE and (self.external or self.cost_estimate_usd):
            raise ValueError("rules must be local and have zero estimated API cost")

    def descriptor(self) -> dict[str, Any]:
        return {"id": self.provider_id, "stage": self.stage.value,
                "implementation": self.implementation_id, "external": self.external,
                "cost_estimate_usd": self.cost_estimate_usd}


@dataclass(frozen=True)
class RoutingPolicy:
    policy_id: str
    task: str
    choices: tuple[str, ...]
    min_confidence: float = 0.9
    max_attempts: int = 3
    allow_external: bool = False
    require_human: bool = True

    def __post_init__(self) -> None:
        identifier(self.policy_id)
        # Reuse request validation for the trusted task/choice schema.
        DecisionRequest("schema", self.task, self.choices, "{}")
        number(self.min_confidence, maximum=1.0)
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 8:
            raise ValueError("max_attempts must be an integer in 1..8")
        if type(self.allow_external) is not bool or type(self.require_human) is not bool:
            raise ValueError("policy flags must be boolean")


@dataclass(frozen=True)
class GatewayResult:
    evaluation_id: str
    request_id: str
    outcome: Outcome
    choice: str | None
    confidence: float | None
    reason: str
    attempts: int
    config_digest: str

    @property
    def authorization_granted(self) -> bool:
        """Always false. The caller must independently authorize any side effect."""
        return False
