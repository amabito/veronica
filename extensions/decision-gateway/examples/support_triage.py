"""Local demonstration with a real core and synthetic classifiers; no external API."""
from dataclasses import asdict
import json

from veronica_core.containment import ChainMetadata, ExecutionConfig, ExecutionContext
from veronica_gateway import (
    Assessment, CoreCallBoundary, DecisionGateway, DecisionRequest, MemoryAuditSink,
    Provider, RoutingPolicy, Stage,
)


def local_rule(request: DecisionRequest) -> Assessment:
    if request.context().get("category") == "invoice":
        return Assessment("billing", 1.0)
    return Assessment(None, 0.0)


def main() -> None:
    providers = (
        Provider("local-rule", Stage.RULE, local_rule),
        # Intentionally synthetic fixtures, NOT actual model or price estimates.
        Provider("demo-light", Stage.LIGHT, lambda _: Assessment("technical", 0.6)),
        Provider("demo-frontier", Stage.FRONTIER, lambda _: Assessment("technical", 0.95)),
    )
    policy = RoutingPolicy("support-v1", "support-triage", ("billing", "technical"))
    for number, category in enumerate(("invoice", "unknown"), 1):
        request = DecisionRequest(f"demo-{number}", policy.task, policy.choices,
                                  json.dumps({"category": category}))
        sink = MemoryAuditSink()
        with ExecutionContext(
            config=ExecutionConfig(max_cost_usd=0.10, max_steps=4,
                                   max_retries_total=1, timeout_ms=5000),
            metadata=ChainMetadata(request_id=request.request_id, chain_id="support-demo"),
        ) as context:
            gateway = DecisionGateway(policy, providers, boundary=CoreCallBoundary(context),
                                      audit=sink)
            result = gateway.evaluate(request)
            print(json.dumps({"result": asdict(result),
                              "authorization_granted": result.authorization_granted,
                              "audit_events": len(sink.events)}, sort_keys=True))


if __name__ == "__main__":
    main()
