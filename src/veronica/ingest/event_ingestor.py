# src/veronica/ingest/event_ingestor.py
"""EventIngestor -- converts kernel SafetyEvents to CP StepOutcomes.

Architecture
------------
The control plane (VeronicaOS) emits kernel-level SafetyEvents via
veronica-core's BufferedEmitter. EventIngestor subscribes to those events,
converts each to a CP-level StepOutcome (schemas.events), and writes
the record to a Store.

Thread-safety
-------------
All public methods are thread-safe. The internal batch list is protected
by a lock. Flush is idempotent; concurrent flushes serialize on the lock.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from typing import Any, Mapping, Sequence

from veronica_core.shield.event import SafetyEvent
from veronica_core.shield.hooks import Decision

from veronica.schemas.events import StepOutcome as CPStepOutcome

logger = logging.getLogger(__name__)

_WRITE_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.2)

# Map veronica-core Decision enum -> CP decision literal
_DECISION_MAP: dict[str, str] = {
    Decision.ALLOW.value: "allow",
    Decision.HALT.value: "halt",
    Decision.DEGRADE.value: "degrade",
    Decision.RETRY.value: "retry",
    Decision.QUARANTINE.value: "quarantine",
    Decision.QUEUE.value: "queue",
}

_DEFAULT_DECISION = "unknown"


def _map_decision(decision: Decision) -> str:
    """Convert a kernel Decision to a CP decision string.

    Returns "unknown" (not "allow") for unrecognized decisions to avoid
    silently granting access on unmapped values.
    """
    try:
        return _DECISION_MAP.get(decision.value, _DEFAULT_DECISION)
    except AttributeError:
        return _DEFAULT_DECISION


def _derive_policy_hash(event: SafetyEvent) -> str:
    """Derive a stable policy_hash from a SafetyEvent.

    If the event metadata contains a pre-computed policy_hash, use it.
    Otherwise compute a deterministic SHA-256 from the hook name and
    event_type, which is stable across identical events.
    """
    meta = event.metadata or {}
    if "policy_hash" in meta:
        return str(meta["policy_hash"])
    raw = f"{event.hook}:{event.event_type}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _derive_audit_id(event: SafetyEvent) -> str:
    """Return the audit_id from event metadata, or generate a new UUID4."""
    meta = event.metadata or {}
    if "audit_id" in meta:
        return str(meta["audit_id"])
    return uuid.uuid4().hex


def _convert_event(
    event: SafetyEvent,
    operation_name: str | None = None,
) -> CPStepOutcome:
    """Convert one kernel SafetyEvent to a CP StepOutcome."""
    meta = event.metadata or {}
    step_id = meta.get("step_id", event.request_id or uuid.uuid4().hex)
    chain_id = meta.get("chain_id", "unknown")
    op_name = operation_name or meta.get("operation_name", event.hook)
    cost_usd = float(meta.get("cost_usd", 0.0))
    tokens = int(meta.get("tokens", meta.get("tokens_in", 0)) or 0) + int(
        meta.get("tokens_out", 0) or 0
    )
    duration_ms = float(meta.get("duration_ms", meta.get("elapsed_ms", 0.0)) or 0.0)
    try:
        ts = event.ts.timestamp() if event.ts is not None else time.time()
    except AttributeError:
        ts = time.time()

    return CPStepOutcome(
        step_id=str(step_id),
        chain_id=str(chain_id),
        operation_name=str(op_name),
        decision=_map_decision(event.decision),  # type: ignore[arg-type]
        cost_usd=cost_usd,
        tokens=tokens,
        duration_ms=duration_ms,
        policy_hash=_derive_policy_hash(event),
        audit_id=_derive_audit_id(event),
        timestamp=ts,
    )


class CPStepOutcomeStore:
    """Minimal in-memory store for CP StepOutcomes.

    Used as the default store for EventIngestor when no external store
    is provided. Suitable for testing and single-process use.
    Thread-safe append and snapshot.
    """

    def __init__(self) -> None:
        self._records: list[CPStepOutcome] = []
        self._lock = threading.Lock()

    def put(self, outcome: CPStepOutcome) -> None:
        with self._lock:
            self._records.append(outcome)

    def put_many(self, outcomes: Sequence[CPStepOutcome]) -> None:
        with self._lock:
            self._records.extend(outcomes)

    def snapshot(self) -> list[CPStepOutcome]:
        with self._lock:
            return list(self._records)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


class EventIngestor:
    """Subscribes to kernel SafetyEvents; converts to CP StepOutcomes.

    Usage
    -----
    >>> store = CPStepOutcomeStore()
    >>> ingestor = EventIngestor(store=store, batch_size=10)
    >>> emitter.subscribe("cp_ingestor", ingestor.on_event)
    >>> # ... events flow in via on_event() ...
    >>> ingestor.flush()  # flush any remaining batch

    Parameters
    ----------
    store       : must implement put_many(Sequence[CPStepOutcome]).
                  Defaults to an in-process CPStepOutcomeStore.
    batch_size  : flush to store after this many events. 0 = no batching
                  (flush on every event). Default: 50.
    operation_name : fixed override for operation_name field. If None,
                  derived from event metadata or hook name.
    """

    _DEAD_LETTER_MAX: int = 1000

    def __init__(
        self,
        store: CPStepOutcomeStore | None = None,
        batch_size: int = 50,
        operation_name: str | None = None,
    ) -> None:
        self._store = store if store is not None else CPStepOutcomeStore()
        self._batch_size = max(0, batch_size)
        self._operation_name = operation_name
        self._batch: list[CPStepOutcome] = []
        self._lock = threading.Lock()
        self._ingested_total: int = 0
        self._error_total: int = 0
        self._dead_letter: list[CPStepOutcome] = []

    @property
    def ingested_total(self) -> int:
        """Total events successfully converted and queued."""
        with self._lock:
            return self._ingested_total

    @property
    def error_total(self) -> int:
        """Total events that failed conversion."""
        with self._lock:
            return self._error_total

    @property
    def dead_letter_count(self) -> int:
        """Number of outcomes held in the dead-letter buffer (store write failures)."""
        with self._lock:
            return len(self._dead_letter)

    def on_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        """BufferedEmitter subscriber callback.

        Accepts the (event_type, payload) signature of EventEmitterProtocol.
        Only processes payloads that contain a kernel SafetyEvent under
        the key 'safety_event'. Other event shapes are silently ignored.
        """
        safety_event: SafetyEvent | None = payload.get("safety_event")  # type: ignore[assignment]
        if safety_event is None:
            return
        self.ingest(safety_event)

    def ingest(self, event: SafetyEvent) -> None:
        """Convert one SafetyEvent and append to batch. Flush if full."""
        try:
            outcome = _convert_event(event, self._operation_name)
        except Exception:
            event_type_label = getattr(event, "event_type", "<unknown>")
            logger.exception("[EventIngestor] conversion failed for event_type=%s", event_type_label)
            with self._lock:
                self._error_total += 1
            return

        flush_now: list[CPStepOutcome] | None = None
        with self._lock:
            self._ingested_total += 1
            # Count outcomes with unmapped decisions as errors
            if outcome.decision == "unknown":
                logger.warning(
                    "[EventIngestor] unrecognized decision for event_type=%s; "
                    "mapped to 'unknown' (not 'allow')",
                    getattr(event, "event_type", "<unknown>"),
                )
                self._error_total += 1
            if self._batch_size == 0:
                # No batching: flush immediately (single item)
                flush_now = [outcome]
            else:
                self._batch.append(outcome)
                if len(self._batch) >= self._batch_size:
                    flush_now = self._batch
                    self._batch = []

        if flush_now is not None:
            self._write(flush_now)

    def ingest_many(self, events: Sequence[SafetyEvent]) -> None:
        """Batch ingest multiple SafetyEvents."""
        for event in events:
            self.ingest(event)

    def flush(self) -> int:
        """Write any buffered outcomes to the store.

        Returns the number of records successfully persisted.
        """
        with self._lock:
            pending = self._batch
            self._batch = []

        if not pending:
            return 0

        return self._write(pending)

    def _write(self, outcomes: list[CPStepOutcome]) -> int:
        """Write outcomes to store with retry. Returns count of persisted records."""
        delays = _WRITE_RETRY_DELAYS
        for attempt in range(len(delays) + 1):
            try:
                self._store.put_many(outcomes)
                return len(outcomes)
            except Exception:
                if attempt < len(delays):
                    time.sleep(delays[attempt])

        logger.exception(
            "[EventIngestor] store.put_many failed after retries, %d records moved to dead-letter",
            len(outcomes),
        )
        with self._lock:
            self._error_total += len(outcomes)
            available = self._DEAD_LETTER_MAX - len(self._dead_letter)
            if available > 0:
                self._dead_letter.extend(outcomes[:available])
        return 0

    @property
    def store(self) -> CPStepOutcomeStore:
        """Direct access to the underlying store."""
        return self._store
