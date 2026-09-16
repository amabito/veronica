"""Autonomous intake -> ledger -> preserved archive -> verification -> durable report.

Not a general-purpose agent: fixed tools operate within explicit, local delegation.
A failed/unknown state never causes blind repetition of a non-idempotent operation.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol, TypeVar
from uuid import uuid4
import os

from ..contracts import canonical, digest
from .contracts import Delegation, Document, KINDS, SUFFIXES, ReviewRequired, Stopped
from .extract import extract_document
from .filesystem import content_hash, no_links, read_regular, write_once, workspace_lock
from .store import Store

T = TypeVar("T")


class ToolBoundary(Protocol):
    def call(self, operation: str, fn: Callable[[], T]) -> T: ...


class CoreToolBoundary:
    """Capture result separately from core Decision. Do NOT auto-retry tool calls."""
    def __init__(self, context):
        from veronica_core.containment import ExecutionContext
        if not isinstance(context, ExecutionContext):
            raise TypeError("real_execution_context_required")
        self.context = context

    def call(self, operation, fn):
        from veronica_core.containment import WrapOptions
        from veronica_core.shield.types import Decision
        result, errors = [], []
        invoked = False
        def invoke():
            nonlocal invoked
            if invoked:
                raise Stopped("duplicate_tool_invocation")
            invoked = True
            try:
                result.append(fn())
            except Exception as error:
                errors.append(error)
                raise
        decision = self.context.wrap_tool_call(
            invoke, options=WrapOptions(operation_name=f"autopilot:{operation}",
                                        retry_policy_override=0))
        if errors:
            # Recoverable document/IO errors retain their type; never silently retry.
            raise errors[0]
        if (decision is not Decision.ALLOW or self.context.get_snapshot().aborted
                or len(result) != 1):
            raise Stopped("kernel_stopped_operation")
        return result[0]


def initialize(workspace: Path, issuers: tuple[str, ...], *, valid_days: int = 30) -> Path:
    """Explicit installation action. Refuse to repurpose any nonempty directory."""
    workspace = Path(os.path.abspath(workspace))
    no_links(workspace)
    if type(valid_days) is not int or not 1 <= valid_days <= 365:
        raise ValueError("invalid_valid_days")
    policy = Delegation(uuid4().hex, str(workspace), issuers, KINDS,
                        (datetime.now(timezone.utc) + timedelta(days=valid_days)).isoformat())
    workspace.mkdir(parents=True, exist_ok=True)
    if any(workspace.iterdir()):
        raise ValueError("workspace_must_be_empty")
    for name in ("inbox", "archive", "reports", ".veronica"):
        (workspace / name).mkdir(mode=0o700)
    for kind in KINDS:
        (workspace / "archive" / kind).mkdir(mode=0o700)
    path = workspace / ".veronica" / "delegation.json"
    write_once(path, (canonical(asdict(policy)) + "\n").encode())
    return path


class Autopilot:
    """Fixed-workflow runner. Boundary factory injection is for trusted integrations/tests."""
    def __init__(self, policy_path: Path, *, boundary_factory=None,
                 extractor=extract_document):
        self.policy_path = Path(os.path.abspath(policy_path))
        self.policy_bytes = read_regular(self.policy_path, 65536)
        self.policy = Delegation.load(self.policy_bytes.decode("utf-8"))
        self.root = Path(self.policy.workspace)
        if self.policy_path != self.root / ".veronica" / "delegation.json":
            raise Stopped("delegation_path_mismatch")
        self._boundary_factory = boundary_factory
        self._extractor = extractor  # trusted host adapter, not supplied by a document
        self._validate_workspace()

    def _validate_workspace(self):
        for rel in (".", "inbox", "archive", "reports", ".veronica",
                    *(f"archive/{k}" for k in KINDS)):
            path = self.root / rel
            no_links(path)
            if not path.is_dir():
                raise Stopped("missing_workspace_directory")
        if str(self.root).startswith(("//", "\\\\")):
            raise Stopped("network_workspace_not_supported")

    def _authorize(self, operation, record=None):
        self._validate_workspace()
        if os.path.lexists(self.root / ".veronica" / "STOP"):
            raise Stopped("operator_stop")
        if read_regular(self.policy_path, 65536) != self.policy_bytes:
            raise Stopped("delegation_changed_reload_required")
        self.policy.authorize(operation, record)

    def run_once(self):
        for operation in ("read_inbox", "register_ledger", "archive_copy", "verify", "write_report"):
            self._authorize(operation)
        run_id = uuid4().hex
        if self._boundary_factory is None:
            # Import before touching the work DB. No dependency => no fake enforcement.
            from veronica_core.containment import ChainMetadata, ExecutionConfig, ExecutionContext
            with ExecutionContext(
                config=ExecutionConfig(max_cost_usd=0.01,
                                       max_steps=32 + self.policy.max_documents * 12,
                                       max_retries_total=1 + self.policy.max_documents,
                                       timeout_ms=60_000 + self.policy.max_documents * 15_000),
                metadata=ChainMetadata(request_id=run_id, chain_id=self.policy.delegation_id),
            ) as context:
                return self._run(run_id, CoreToolBoundary(context))
        return self._run(run_id, self._boundary_factory(run_id))

    def _run(self, run_id, boundary):
        self._authorize("read_inbox")
        with workspace_lock(self.root / ".veronica" / "runner.lock"):
            opened = []
            def open_store():
                store = Store(self.root / ".veronica" / "state.sqlite3")
                opened.append(store)
                return store
            try:
                store = boundary.call("read_inbox", open_store)
                return self._batch(run_id, boundary, store)
            finally:
                for store in opened:
                    store.close()

    def _batch(self, run_id, boundary, store):
        def action(operation, fn, record=None, job_id=None):
            self._authorize(operation, record)
            store.audit(run_id, job_id, self.policy.fingerprint, operation, "intent")
            def guarded():
                # Recheck immediately before the actual tool, not only before core dispatch.
                self._authorize(operation, record)
                return fn()
            try:
                result = boundary.call(operation, guarded)
            except ReviewRequired as error:
                store.audit(run_id, job_id, self.policy.fingerprint, operation, "review", str(error))
                raise
            except Exception:
                store.audit(run_id, job_id, self.policy.fingerprint, operation, "failed", "operation_failed")
                raise
            store.audit(run_id, job_id, self.policy.fingerprint, operation, "acknowledged")
            return result

        def scan():
            names = []
            with os.scandir(self.root / "inbox") as entries:
                for count, entry in enumerate(entries):
                    if count >= 10000:
                        raise Stopped("inbox_scan_limit")
                    # Do not follow symlinks or recurse; an eligible symlink is rejected on read.
                    if Path(entry.name).suffix.lower() in SUFFIXES:
                        names.append(entry.name)
            names.sort()
            cursor = store.cursor()
            return [n for n in names if n > cursor] + [n for n in names if n <= cursor]

        def reconcile_reports():
            rows = store.db.execute("SELECT run_id,report_json FROM runs ORDER BY rowid DESC LIMIT 100").fetchall()
            for row in rows:
                write_once(self.root / "reports" / f"run-{row['run_id']}.json",
                           (row['report_json'] + "\n").encode())
        action("write_report", reconcile_reports)
        names = action("read_inbox", scan)
        selected = names[:self.policy.max_documents]
        items = []
        for name in selected:
            source_ref = digest(name)  # do not leak raw input filenames in metadata reports
            job_id = None
            try:
                suffix = Path(name).suffix.lower()
                data = action("read_inbox", lambda: read_regular(self.root / "inbox" / name,
                                                                self.policy.max_bytes))
                source_sha = content_hash(data)
                job_id = digest(["document-job-v1", source_sha, suffix])
                job = store.job(job_id)
                previous_review = store.intake_review(job_id, self.policy.fingerprint)
                if job is None and previous_review is not None:
                    raise ReviewRequired(previous_review)
                if job is not None and job['state'] == "REVIEW_REQUIRED":
                    items.append({"source_name": name, "source_ref": source_ref, "job_id": job_id,
                                  "status": "review_required", "reason": job['reason']})
                    continue
                document = (Document.load(job['plan_json']) if job is not None else
                            action("read_inbox", lambda: self._extractor(data, suffix), job_id=job_id))
                if type(document) is not Document:
                    raise ReviewRequired("extractor_contract_violation")
                document = Document.load(document.json)
                # Bind business record to current delegation before ANY ledger/archive mutation.
                self.policy.authorize("register_ledger", document)
                archive_rel = f"archive/{document.kind}/{source_sha}{suffix}"
                if job is None:
                    action("register_ledger", lambda: store.enroll(
                        job_id, source_sha, suffix, document, archive_rel, self.policy.fingerprint),
                        document, job_id)
                    job = store.job(job_id)
                already = job['state'] == "COMPLETED"
                if job['state'] not in ("READY", "REGISTERED", "ARCHIVED", "VERIFIED", "COMPLETED"):
                    raise Stopped("unknown_job_state")
                if job['state'] == "READY":
                    action("register_ledger", lambda: store.register(job_id, document), document, job_id)
                    job = store.job(job_id)
                if job['state'] == "REGISTERED":
                    def archive():
                        write_once(self.root / archive_rel, data)
                        store.set_state(job_id, "ARCHIVED")
                    action("archive_copy", archive, document, job_id)

                def verify():
                    store.verify_ledger(document)
                    if read_regular(self.root / archive_rel, self.policy.max_bytes) != data:
                        raise ReviewRequired("archive_verification_failed")
                    if read_regular(self.root / "inbox" / name, self.policy.max_bytes) != data:
                        raise ReviewRequired("source_changed_before_completion")
                action("verify", verify, document, job_id)

                def complete():
                    body = {"schema_version": 1, "job_id": job_id, "source_sha": source_sha,
                            "business_key": document.business_key, "kind": document.kind,
                            "archive": archive_rel, "status": "completed",
                            "policy_sha": self.policy.fingerprint,
                            "verified_at": datetime.now(timezone.utc).isoformat()}
                    receipt = store.prepare_completion(job_id, body)
                    write_once(self.root / "reports" / f"{job_id}.json", (receipt + "\n").encode())
                    store.set_state(job_id, "COMPLETED")
                action("write_report", complete, document, job_id)
                items.append({"source_name": name, "source_ref": source_ref, "job_id": job_id,
                              "status": "already_completed" if already else "completed"})
            except ReviewRequired as error:
                # Terminal review states are not retried unattended. Other documents proceed.
                if job_id is not None and store.job(job_id) is not None:
                    store.set_state(job_id, "REVIEW_REQUIRED", str(error))
                elif job_id is not None:
                    store.save_intake_review(job_id, str(error), self.policy.fingerprint)
                store.audit(run_id, job_id, self.policy.fingerprint, "verify", "review", str(error))
                items.append({"source_name": name, "source_ref": source_ref, "job_id": job_id,
                              "status": "review_required", "reason": str(error)})
            except OSError:
                # Keep stage unchanged: later runs reconcile deterministic effects, not blind retries.
                store.audit(run_id, job_id, self.policy.fingerprint, "verify", "retryable", "io_unavailable")
                items.append({"source_name": name, "source_ref": source_ref, "job_id": job_id,
                              "status": "retryable", "reason": "io_unavailable"})
            except Stopped:
                raise
            except Exception:
                # Unknown provider/parser/integration errors halt rather than inventing success.
                raise Stopped("unexpected_workflow_error") from None

        counts = {status: sum(i['status'] == status for i in items)
                  for status in ("completed", "already_completed", "review_required", "retryable")}
        pending = dict(store.db.execute("SELECT state,COUNT(*) FROM jobs WHERE state!='COMPLETED' GROUP BY state").fetchall())
        report = {"schema_version": 1, "run_id": run_id, "policy_sha": self.policy.fingerprint,
                  "pending_jobs": pending,
                  "counts": counts, "deferred": max(0, len(names) - len(selected)), "items": items}
        def publish():
            # Store the exact report before publication; an interrupted publish can be reconciled.
            store.save_run(run_id, report)
            write_once(self.root / "reports" / f"run-{run_id}.json", (canonical(report) + "\n").encode())
            if selected:
                store.set_cursor(selected[-1])
        action("write_report", publish)
        return report
