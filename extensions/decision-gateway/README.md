# VERONICA Gateway + Document Autopilot (experimental)

**The document workflow now executes the delegated job, not just a recommendation.**
The operator delegates a specific empty local workspace and permitted issuers once.
Valid supported documents then flow through intake, SQLite ledger registration,
non-destructive archival, read-back verification and durable JSON reporting without
per-document approval dialogs. Exceptions are reported separately.

Two separate components are intentionally retained:

- `veronica_gateway.DecisionGateway`: the original advisory model-routing SDK.
  It never grants permission. Its design and usage remain in
  [DECISION_GATEWAY.md](DECISION_GATEWAY.md).
- `veronica_gateway.autopilot.Autopilot`: the new fixed-workflow executor, which
  obtains permission from an explicit local `Delegation`, **not model confidence**.
  It wraps real operations with `veronica-core` and persists real workflow state.

This remains an isolated optional extension. It does not change the existing
control-plane Planner, API routes, deployment, core enforcement, secrets or main
package version. No live model calls, email sends, payments, remote service changes,
agent desktop automation or hosted SaaS deployment are included.

## Run a real end-to-end demonstration

From the repository root, with Python 3.10+ (CI qualification is described below):

```bash
python -m pip install -e "./extensions/decision-gateway[kernel,documents,test]"
python -m veronica_gateway.autopilot demo --workspace ./veronica-autopilot-demo
```

The demo requires a **new empty directory**. It creates synthetic documents, imports
one document into an actual SQLite ledger, recognizes its duplicate, sends an
unstructured document to review, and runs a second time to verify deduplication.
It uses the real core; there is no unprotected fallback when core is unavailable.
No network service, model credential or paid API is required at runtime.
The `documents` extra pins `pypdf==6.18.1`; JSON/text require no PDF library.

## Delegate a workspace and leave the runner operating

Example for Windows (use a local directory, not a shared network drive):

```powershell
python -m veronica_gateway.autopilot init --workspace C:/veronica/work --issuer "Example Supplier"
python -m veronica_gateway.autopilot watch --policy C:/veronica/work/.veronica/delegation.json
```

`init` is the explicit delegation action. Repeat `--issuer` for each exact permitted
issuer. Default validity is 30 days, adjustable with `--valid-days` (1 to 365).
A filename or document cannot expand the workspace, tool list or issuer allowlist.
The issuer field is a content filter, **not authenticated proof of who sent a file**.
Secure the inbox producers and workspace with operating-system permissions.

Place supported files in `inbox/`. `watch` stays in the foreground, scans every
10 seconds by default, and works only while that process is running. It is not an
installed daemon or a remotely running task. `--interval` accepts 1 to 3600 seconds.
For a single batch:

```powershell
python -m veronica_gateway.autopilot run --policy C:/veronica/work/.veronica/delegation.json
```

There is no approval prompt within the allowed workflow. An operator stops it with
Ctrl+C or by creating `.veronica/STOP`. STOP, expiry, changed delegation bytes,
missing permissions or core denial stop new actions. Reload after a policy change.
An operation already in progress cannot be undone by revoking subsequent permission.

Exit codes: 0 = selected work completed without outstanding/deferred work;
2 = review, retryable, pending or deferred work exists; 3 = runner stopped or setup
failed; 130 = operator interrupt. `watch` reports ordinary document exceptions and
continues with other documents, but stops on authority/core/audit failures.

## Supported documents (explicitly limited)

JSON accepts the exact fields in this example. The first five are required; amount
and currency must either both be absent or both be valid. Amounts are decimal
**strings**, never floating-point numbers. Supported currencies are JPY, USD and EUR;
no tax calculation, settlement or currency conversion is performed.

```json
{
  "document_id": "INV-2026-001",
  "kind": "invoice",
  "issuer": "Example Supplier",
  "document_date": "2026-09-16",
  "title": "Office supplies",
  "amount": "12000",
  "currency": "JPY"
}
```

Kinds are `invoice`, `receipt`, `order` and `report`. Restrict `kinds` in the trusted
policy to narrow the workflow. Unknown kinds, extra or duplicate fields, invalid
dates, ambiguous records or disallowed issuers are not automatically registered.

UTF-8 text and text-based PDF use one `field: value` per line, without an unrelated
header/footer or multiline value. English field names above are accepted. Japanese
aliases are also supported:

```text
書類番号：INV-2026-001
種別：invoice
発行者：Example Supplier
日付：2026-09-16
件名：備品
金額：12000
通貨：JPY
```

This is a strict template adapter, **not an AI that understands arbitrary invoices**.
Scanned/image-only PDFs, encrypted PDFs and unfamiliar layouts require review; no
OCR or guessed extraction is substituted. The PDF parser runs in a disposable
process with a 10-second parent deadline, a 20-page limit and a 65,536-character
output limit. POSIX resource bounds are best effort; Windows has the deadline but
no implemented process-memory quota. Do not treat this as a hardened parser sandbox.

## Outputs and resumable state

```text
workspace/
  inbox/                         original files remain untouched
  archive/<kind>/<sha>.<suffix>   verified content-addressed copies
  reports/<job-id>.json           durable per-document completion receipt
  reports/run-<run-id>.json       readable results, filenames and exceptions
  .veronica/delegation.json       trusted operator configuration
  .veronica/state.sqlite3         ledger, job journal, audit and exact run reports
  .veronica/runner.lock           OS-owned single-runner lock
```

The local ledger is SQLite, **not an Excel workbook or an external accounting
system**. The `ledger` table stores canonical document records. No existing row is
silently updated. Reports are local files/stdout, not email or push notifications.

State progression:

```
READY -> REGISTERED -> ARCHIVED -> VERIFIED -> COMPLETED
                       |
                    exceptions -> REVIEW_REQUIRED
```

Business identity is the exact `(kind, issuer, document_id)` tuple after Unicode
normalization. Its unique database key prevents duplicate ledger rows. Identical
file bytes share a job; the same semantic record with different byte formatting
may have multiple preserved archives but still one ledger entry. A different record
for an existing business identity is a conflict, not permission to overwrite it.

SQLite registration and the journal transition commit in one transaction. Archive
and report paths are deterministic, atomically published without overwriting an
existing different file, then read back. A crash after a committed registration or
a published copy is reconciled on the next run; it is not blindly repeated.
Reserved `.veronica-pending-*` temporary links are reconciled after a publish crash.
The OS releases the runner lock even if the process dies.

Completion requires ledger read-back equality, archive byte equality and original
source byte equality. Repeated completed jobs are reverified. Post-completion
archive/ledger/receipt conflicts are surfaced and not overwritten. Unfinished jobs
remain visible in `pending_jobs` even if their source disappears.

An audit intent is durably acknowledged before each document operation. If audit
fails after a side effect, that effect cannot be undone; the run stops and the
journal/receipt is the recovery evidence. This is **not a cross-filesystem/SQLite
atomic transaction** or a general exactly-once remote tool protocol.
The exact final run report is saved in SQLite before publication; the most recent
100 reports are reconciled at the next run. Older stored reports remain in SQLite.

Review states are not repeatedly executed unattended. Correcting an input changes
its content hash and creates a new candidate. A changed authorized policy permits
re-evaluation of cached intake rejection. Existing ledger/archival conflicts require
operator reconciliation; no destructive automatic repair or reviewer UI is included.

## Delegation, limits and trust boundary

The built-in operations are `read_inbox`, `register_ledger`, `archive_copy`, `verify`
and `write_report`. The whole workflow's permissions are checked before opening the
work database and again immediately before each tool. One real `ExecutionContext`
is shared across a batch. HALT, RETRY, DEGRADE, exceptions and missing callback
execution do not enable an alternate unprotected execution route.

Default batch size is 100, configurable up to 1,000. Default maximum document size
is 5 MiB, configurable up to 10 MiB. Inbox discovery is top-level only and bounded
at 10,000 entries. A persisted round-robin cursor prevents earlier completed files
from permanently starving later files. `deferred` means not examined in this batch,
not necessarily an unprocessed business document.

The starter storage stops at 100,000 audit events or 10,000 run reports rather than
silently discarding them. `watch` rechecks files and writes audit/reports even for
replays; it is not an indefinite-retention service. Archive storage is not quota
managed in this MVP. Operators must monitor disk capacity and archive workspace
state appropriately; no automatic record deletion is authorized.

Only service-owned local filesystems supporting hard links are intended (for
example local NTFS or a POSIX filesystem). Symlinks, reparse points and hardlinked
input/DB files are rejected. This is not a network-share or multi-host coordinator.
The lock coordinates participating local processes, not arbitrary external software.

The process, installed code, custom adapters, policy and state directories are
trusted. An attacker with the same OS account can alter code, race directory changes
or manipulate SQLite; this implementation is not a security boundary against them.
The local policy digest is not a signature. Protect it with an OS service account
and permissions. Audit is not tamper-proof or encrypted, and input/record metadata
and filenames are sensitive local data. The core's cancellation semantics are not
changed; arbitrary Python callbacks are not forcibly terminated by the tool adapter.

The autopilot currently needs **no model**, because this workflow can be completed
with deterministic extraction. Future model adapters must return validated records
and must not supply paths, tool names or execution permissions. No accuracy or
cost-savings claims have been measured on a customer dataset.

## Verification

```bash
cd extensions/decision-gateway
python -m pytest -q
```

`test_autopilot.py` uses real temporary files/SQLite but an explicitly test-only
boundary; it covers deduplication, conflicting business identity, revoked delegation,
read-back tampering, actual subprocess death after a database commit and after
hard-link publication, lock release, PDF extraction, batch fairness and review.
`test_autopilot_core.py` tests the actual kernel and actual CLI demo; without core,
that module skips explicitly rather than claiming qualification. PDF tests require
the documents extra. CI imports pinned real core before running any integration tests.

Existing decision-gateway tests retain their original 95% coverage gate, isolated
from the added modules. Autopilot tests have their own 80% coverage gate on Linux
and Windows/Python 3.12. Coverage is not proof of correctness, safety, authenticity,
commercial readiness or complete regression coverage of the control plane.
