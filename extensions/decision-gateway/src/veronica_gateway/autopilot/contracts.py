"""Explicit, local delegation and validated document records; never model authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import re
import unicodedata

from ..contracts import canonical, digest

OPERATIONS = ("read_inbox", "register_ledger", "archive_copy", "verify", "write_report")
KINDS = ("invoice", "receipt", "order", "report")
SUFFIXES = (".json", ".txt", ".pdf")


class ReviewRequired(ValueError):
    """A stable reason code, deliberately excluding raw customer data."""
    def __init__(self, code: str):
        if type(code) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) is None:
            code = "document_review_required"
        super().__init__(code)


class Stopped(RuntimeError):
    """Stop the run; an operation may need reconciliation before reuse."""


def strict_json(text: str):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("duplicate_key")
            result[k] = v
        return result
    def invalid(_):
        raise ValueError("nonfinite")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def clean_text(value: str, limit: int = 200) -> str:
    if (type(value) is not str or not 1 <= len(value) <= limit
            or value != value.strip()
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise ValueError("invalid_text")
    return unicodedata.normalize("NFC", value)


def utc(text: str) -> datetime:
    if type(text) is not str:
        raise ValueError("invalid_expiry")
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("expiry_needs_timezone")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Delegation:
    delegation_id: str
    workspace: str
    issuers: tuple[str, ...]
    kinds: tuple[str, ...]
    expires_at: str
    operations: tuple[str, ...] = OPERATIONS
    max_documents: int = 100
    max_bytes: int = 5_242_880
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.delegation_id)):
            raise ValueError("invalid_delegation")
        p = Path(self.workspace)
        if not p.is_absolute() or str(p) != str(p.resolve()):
            raise ValueError("workspace_must_be_canonical_absolute")
        for field, allowed in ((self.issuers, None), (self.kinds, KINDS),
                               (self.operations, OPERATIONS)):
            if type(field) is not tuple or not field or len(set(field)) != len(field):
                raise ValueError("invalid_allowlist")
            for value in field:
                if clean_text(value) != value or (allowed is not None and value not in allowed):
                    raise ValueError("invalid_allowlist_value")
        for value, maximum in ((self.max_documents, 1000), (self.max_bytes, 10_485_760)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid_limit")
        utc(self.expires_at)

    @classmethod
    def load(cls, text: str):
        data = strict_json(text)
        if type(data) is not dict:
            raise ValueError("invalid_delegation")
        for key in ("issuers", "kinds", "operations"):
            if key in data:
                if type(data[key]) is not list:
                    raise ValueError("invalid_allowlist")
                data[key] = tuple(data[key])
        return cls(**data)

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))

    def authorize(self, operation: str, record: Document | None = None):
        if datetime.now(timezone.utc) >= utc(self.expires_at):
            raise Stopped("delegation_expired")
        if operation not in self.operations:
            raise Stopped("operation_not_delegated")
        if record is not None:
            if record.kind not in self.kinds or record.issuer not in self.issuers:
                raise ReviewRequired("document_outside_delegation")


@dataclass(frozen=True)
class Document:
    document_id: str
    kind: str
    issuer: str
    document_date: str
    title: str
    amount: str | None = None
    currency: str | None = None

    def __post_init__(self):
        for key in ("document_id", "issuer", "title"):
            value = clean_text(getattr(self, key))
            object.__setattr__(self, key, value)
        if self.kind not in KINDS or type(self.kind) is not str:
            raise ValueError("invalid_kind")
        if (type(self.document_date) is not str
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.document_date)):
            raise ValueError("invalid_date")
        date.fromisoformat(self.document_date)
        if self.amount is None and self.currency is None:
            return
        if (type(self.amount) is not str
                or not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?", self.amount)
                or self.currency not in ("JPY", "USD", "EUR")):
            raise ValueError("invalid_amount_or_currency")
        try:
            value = Decimal(self.amount)
            if self.currency == "JPY" and value != value.to_integral():
                raise ValueError("fractional_jpy")
            normalized = format(value, ".0f" if self.currency == "JPY" else ".2f")
        except InvalidOperation:
            raise ValueError("invalid_amount") from None
        object.__setattr__(self, "amount", normalized)

    @classmethod
    def load(cls, text: str):
        try:
            data = strict_json(text)
            if type(data) is not dict:
                raise ValueError("not_object")
            return cls(**data)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise ReviewRequired("invalid_document_fields") from None

    @property
    def json(self) -> str:
        return canonical(asdict(self))

    @property
    def business_key(self) -> str:
        return digest([self.kind, self.issuer, self.document_id])
