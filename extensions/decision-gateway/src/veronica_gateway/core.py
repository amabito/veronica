"""Optional veronica-core adapter; never introduces a second enforcement kernel."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import Assessment, DecisionRequest, Provider
from .engine import BoundaryDenied

if TYPE_CHECKING:
    from veronica_core.containment import ExecutionContext


class CoreCallBoundary:
    """Reuse ONE caller-owned ExecutionContext throughout a request's cascade.

    Use the real kernel's ALLOW return, not callback truthiness. HALT, RETRY,
    DEGRADE, exceptions, missing callback execution and post-call aborts cannot
    release an assessment. The context/pipeline remains the policy authority.

    Provider callbacks are trusted in-process integration code, NOT sandboxed.
    Configure network deadlines at each provider; this adapter does not kill
    arbitrary Python functions. Cost hints are estimates, not invoice receipts.
    """
    def __init__(self, context: ExecutionContext) -> None:
        from veronica_core.containment import ExecutionContext, WrapOptions
        from veronica_core.shield.types import Decision

        if not isinstance(context, ExecutionContext):
            raise TypeError("a real veronica-core ExecutionContext is required")
        self._context = context
        self._options_type = WrapOptions
        self._allow = Decision.ALLOW

    def call(self, provider: Provider, request: DecisionRequest) -> Assessment:
        before = self._context.get_snapshot()
        if before.request_id != request.request_id or before.aborted:
            raise BoundaryDenied("request binding mismatch or aborted context")
        results: list[Assessment] = []
        invoked = False

        def invoke() -> None:
            nonlocal invoked
            if invoked:
                raise BoundaryDenied("repeated provider invocation rejected")
            invoked = True
            results.append(provider.assess(request))

        # Built-in estimate/reservation/accounting is used as-is. No local budget
        # counter, retry loop, dynamic policy rewrite, or weakened fallback.
        decision = self._context.wrap_llm_call(
            invoke,
            options=self._options_type(
                operation_name=f"decision:{provider.provider_id}",
                cost_estimate_hint=provider.cost_estimate_usd,
                retry_policy_override=0,
            ),
        )
        after = self._context.get_snapshot()
        if (decision is not self._allow or after.aborted
                or after.request_id != request.request_id or len(results) != 1):
            raise BoundaryDenied("kernel did not allow a completed assessment")
        return results[0]
