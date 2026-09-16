# VERONICA Decision Gateway -- experimental application-side extension

A provider-neutral **advisory decision cascade**: local rule -> lightweight
assessment -> frontier assessment -> human review. This is the first implementation
slice, not a hosted service, an agent OS, an authorization server, or a billing product.

This package is deliberately separate from both `veronica-core` and the existing
control-plane `Planner`. It does not change their defaults, install an HTTP route,
start a service, or import itself into `veronica-cp`.

## Safety boundary

```
Application -> optional DecisionGateway
                 | each assessment call
                 v
          caller-owned ExecutionContext -> registered provider
                 |
                 v
         advisory recommendation / review_required / blocked
                 |
       separate, authorized application workflow
                 |
          core enforcement AGAIN -> any actual tool or side effect
```

**Model confidence never grants permission.** `authorization_granted` is always
false, even for a deterministic rule or a recommendation with confidence 1.0.
The returned outcome `recommendation` means only that an assessment passed the
configured routing threshold. It does not mean an action is allowed, safe or correct.
Default policy requires human review; this package does not implement approval issuance.

Provider scores are not calibrated probabilities of correctness. Calibrate the
threshold on held-out, task-specific data before relying on recommendations in a
workflow. No accuracy, savings, latency or hallucination-free claim is made.

## Implemented

- Frozen Choice request/assessment/policy contracts; canonical bounded JSON input.
  Duplicate JSON keys, NaN/infinity, excessive depth, booleans in numeric fields,
  mutable or duplicate choices and labels outside the schema are rejected.
- Ordered rule/light/frontier cascade, bounded attempts, explicit abstention,
  and escalation to human review. Only valid low-confidence/abstaining responses
  advance to another provider. Errors and kernel denials do not trigger fallback.
- External providers are disabled by default and are configured by trusted code,
  not selected or enabled by the request. Each provider gets a fresh parsed context.
- Mandatory call-boundary and acknowledged audit interfaces; no pass-through
  production boundary and no implicit best-effort audit sink.
- `CoreCallBoundary` reuses the caller's **same** ExecutionContext for the whole
  cascade. Every assessment, including a zero-cost rule, goes through the kernel.
  The request ID must match the context. HALT, RETRY, DEGRADE, missing callback
  execution and post-call abort all withhold the answer. The core returns a control
  decision, not the callback result, so the adapter captures them separately.
- Correlated metadata-only audit records with input/configuration digests. No
  raw input, raw answer, confidence or exception message is recorded by the gateway.
  `MemoryAuditSink` is a bounded, thread-safe demo sink, NOT durable storage.

## Install and test from this repository

This package has not been published by this change. From the repository root:

```bash
python -m pip install -e './extensions/decision-gateway[kernel,test]'
cd extensions/decision-gateway
python -m pytest -q --cov=veronica_gateway --cov-branch
python examples/support_triage.py
```

The kernel integration target inspected for this change is `veronica-core` 3.10.0
at commit `d03aa2fcba32dadcb9a8f22b52ab402af7829ef7`. The workflow pins that source
commit; the optional dependency declares `>=3.10.0,<4`. Older core versions have
not been qualified. The base control plane is `amabito/veronica` commit
`730f42830ac8ccee553d668b54aa950966b9dcbf`.

Without the optional kernel, only the independent and mocked-contract tests run:

```bash
python -m pip install -e './extensions/decision-gateway[test]'
cd extensions/decision-gateway
python -m pytest -q
```

`tests/test_core_contract.py` uses explicitly labelled mocks; it is not evidence
of real-kernel compatibility. `tests/test_core_integration.py` skips explicitly
when the kernel is absent. CI imports the real kernel before pytest so a missing
kernel cannot silently turn a green CI run into mock-only verification.

The example uses local deterministic **demo** classifiers, not an LLM or Jev.
It makes no external calls and uses no credentials. Replace `Provider.assess`
with a trusted adapter only after validating its SDK, response parser, deadlines,
maximum output size and task-specific quality. No vendor API is invented here.

## Minimal wiring

```python
from veronica_core.containment import ChainMetadata, ExecutionConfig, ExecutionContext
from veronica_gateway import (
    Assessment, CoreCallBoundary, DecisionGateway, DecisionRequest,
    MemoryAuditSink, Provider, RoutingPolicy, Stage,
)

request = DecisionRequest("request-1", "support-triage", ("billing", "technical"),
                          '{"category":"invoice"}')
policy = RoutingPolicy("support-v1", "support-triage", request.choices)
rule = Provider("local-rule", Stage.RULE,
                lambda r: Assessment("billing", 1.0)
                if r.context().get("category") == "invoice"
                else Assessment(None, 0.0))

with ExecutionContext(
    config=ExecutionConfig(max_cost_usd=0.10, max_steps=4,
                           max_retries_total=1, timeout_ms=5000),
    metadata=ChainMetadata(request_id=request.request_id, chain_id="support-1"),
) as context:
    gateway = DecisionGateway(policy, (rule,), boundary=CoreCallBoundary(context),
                              audit=MemoryAuditSink())  # demo storage only
    result = gateway.evaluate(request)
    assert result.authorization_granted is False
    # Present the suggestion to an authorized reviewer. No tool action here.
```

## Threat model and important limitations

Provider callbacks, their external/local declarations, the policy, the kernel
context and the audit sink are **trusted host integration code**. Request content
and model output are untrusted data. A malicious in-process plugin can bypass Python
objects or make its own network calls: this is not an OS sandbox or an egress firewall.
The `external` flag is a routing restriction, not a network-isolation mechanism.

The configuration digest binds policy and declared provider metadata, not executable
code, credentials or model weights. It is not signed-policy attestation. Request
hashes are correlatable and may expose low-entropy inputs to guessing; handle them
as sensitive metadata, not anonymized data. Do not put customer content in IDs.

`cost_estimate_usd` is a trusted estimate passed to the kernel's existing cost
hint/reservation/accounting path. It is not a verified upper bound or a billing
receipt. No second budget counter is introduced here. SDK retries, actual token
usage, failed-call charges and multi-request/distributed accounting require a
proper provider/usage integration before commercial metering. This MVP does not
claim to cap an invoice based solely on estimates.

The adapter is synchronous and does not forcibly interrupt arbitrary Python calls.
Use provider-level network deadlines and a process/container boundary where hard
termination is required. The kernel's own configured cancellation semantics remain
unchanged; this extension adds no hard timeout guarantee.

Audit failure before an attempt prevents that call. Audit failure after a call
suppresses the result, but cannot undo an already paid inference. Treat
`AuditUnavailable` as a stop, not a reason to retry automatically. Request IDs are
correlation IDs, not deduplication keys: repeated calls can execute again.
A custom audit sink must acknowledge storage or raise, never silently drop events.
The in-memory sink deliberately stops at capacity instead of discarding records.

There is no HTTP API, authentication/tenant mapping, durable approval workflow,
signed audit export, usage settlement, customer billing, SDK adapter, desktop agent
or legacy software automation in this slice. There are no changes to the core,
control-plane Planner, existing API routes, secrets, deployment or package releases.

## Next qualification gates (not implemented in this slice)

1. Qualify an actual provider on a versioned task dataset; measure errors and
   escalation rate as well as cost/latency. Keep operational authorization separate.
2. Add an authenticated application endpoint, tenant/chain binding, durable audit
   and idempotency/usage settlement; do not make the routing SDK an auth service.
3. Implement durable reviewer decisions bound to the exact input, policy version,
   operation and expiry, followed by core re-enforcement at the actual side effect.
4. Run deployment and control-plane regression tests before enabling this extension
   in an existing application. Paid plans and savings claims require real measurements.
