# Phase 3: RedisArbiter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add RedisArbiter for multi-process budget allocation with 2-phase reserve/settle, without changing Protocol interfaces.

**Architecture:** New `RedisArbiter` class implements `ArbiterProtocol`. Atomic Lua scripts handle reserve/settle in Redis. `os.py` injects context via `hasattr` guards (6 lines). Falls back to `ProportionalArbiter` on Redis failure or missing context.

**Tech Stack:** Python 3.10+, redis-py[hiredis], fakeredis[lua], contextvars, pytest

**Design doc:** `docs/plans/2026-02-27-phase3-redis-arbiter-design.md`

---

## Dependency Graph

```
Task 1 (Lua scripts)     Task 2 (microusd helpers)
          \                    /
           Task 3 (RedisArbiter core)
                    |
          +---------+---------+
          |                   |
Task 4 (unit tests)   Task 5 (os.py wiring)
          |                   |
          +-------------------+
                    |
          Task 6 (atomicity tests)
                    |
          Task 7 (integration tests)
                    |
          Task 8 (__init__.py + pyproject.toml)
                    |
          Task 9 (version bump + final verification)
```

**Parallel opportunities:** Tasks 1 and 2 are independent. Tasks 4 and 5 are independent.

---

### Task 1: Lua Script Constants

**Files:**
- Create: `src/veronica/_redis_scripts.py`

**Step 1: Create the Lua script constants file**

```python
# src/veronica/_redis_scripts.py
"""Lua scripts for RedisArbiter atomic operations."""

LUA_RESERVE = """\
-- KEYS[1] = remaining_key
-- KEYS[2] = alloc_key
-- ARGV[1] = alloc_json (full allocation record)
-- ARGV[2] = reserve_micro (amount to deduct)
-- ARGV[3] = ttl_seconds
-- ARGV[4] = total_budget_micro (initial value if key missing)

-- Idempotency check
local existing = redis.call('GET', KEYS[2])
if existing then
    return existing
end

-- Initialize remaining if first call
local remaining = redis.call('GET', KEYS[1])
if not remaining then
    remaining = ARGV[4]
    redis.call('SET', KEYS[1], remaining)
end
remaining = tonumber(remaining)
local reserve = tonumber(ARGV[2])

-- Budget check
if remaining < reserve then
    return cjson.encode({
        status = "insufficient",
        remaining = remaining,
        requested = reserve
    })
end

-- Reserve: deduct and store allocation record
redis.call('SET', KEYS[2], ARGV[1], 'EX', tonumber(ARGV[3]))
redis.call('SET', KEYS[1], tostring(remaining - reserve))
return ARGV[1]
"""

LUA_SETTLE = """\
-- KEYS[1] = remaining_key
-- KEYS[2] = alloc_key
-- ARGV[1] = actual_cost_micro

local alloc_json = redis.call('GET', KEYS[2])

if not alloc_json then
    -- TTL expired: still deduct actual cost (safe side).
    local remaining = tonumber(redis.call('GET', KEYS[1]))
    local actual = tonumber(ARGV[1])
    local new_remaining = remaining - actual
    if new_remaining < 0 then new_remaining = 0 end
    redis.call('SET', KEYS[1], tostring(new_remaining))
    return cjson.encode({status = "expired", deducted = actual})
end

local alloc = cjson.decode(alloc_json)
local reserved = tonumber(alloc.total_reserved_micro)
local actual = tonumber(ARGV[1])
local diff = reserved - actual

local remaining = tonumber(redis.call('GET', KEYS[1]))

if diff > 0 then
    redis.call('SET', KEYS[1], tostring(remaining + diff))
elseif diff < 0 then
    local new_remaining = remaining + diff
    if new_remaining < 0 then new_remaining = 0 end
    redis.call('SET', KEYS[1], tostring(new_remaining))
end

redis.call('DEL', KEYS[2])
return cjson.encode({status = "settled", diff = diff})
"""
```

**Step 2: Verify the file was created**

Run: `python -c "from veronica._redis_scripts import LUA_RESERVE, LUA_SETTLE; print('OK:', len(LUA_RESERVE), len(LUA_SETTLE))"`
Expected: `OK: <num> <num>` (both positive integers)

**Step 3: Commit**

```bash
git add src/veronica/_redis_scripts.py
git commit -m "feat: add Lua script constants for RedisArbiter"
```

---

### Task 2: microusd Helpers + pyproject.toml Dependencies

**Files:**
- Modify: `pyproject.toml:37-41` (add dependencies)

**Step 1: Add fakeredis[lua] to dev deps and redis optional dep in pyproject.toml**

Edit `pyproject.toml`:

Before:
```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]
```

After:
```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "fakeredis[lua]>=2.0",
]
redis = [
    "redis[hiredis]>=5.0",
]
```

**Step 2: Install dev dependencies**

Run: `cd D:/work/Projects/veronica && pip install -e ".[dev]"`
Expected: Successfully installed fakeredis, lupa (Lua runtime), etc.

**Step 3: Verify fakeredis with Lua works**

Run:
```bash
python -c "
import fakeredis
r = fakeredis.FakeRedis(decode_responses=True)
script = r.register_script('return cjson.encode({status=\"ok\"})')
result = script(keys=[], args=[])
print(result)
"
```
Expected: `{"status":"ok"}` (or similar JSON with status field)

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add fakeredis[lua] dev dep, redis optional dep"
```

---

### Task 3: RedisArbiter Core Implementation

**Files:**
- Create: `src/veronica/redis_arbiter.py`

**Step 1: Write the RedisArbiter class**

```python
# src/veronica/redis_arbiter.py
"""VERONICA OS arbiter -- RedisArbiter for multi-process budget allocation."""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Mapping, Sequence

from veronica._redis_scripts import LUA_RESERVE, LUA_SETTLE
from veronica.proportional_arbiter import ProportionalArbiter
from veronica.types import DesiredPolicy, PolicyConfig

logger = logging.getLogger(__name__)

_MICRO = 1_000_000

_ctx_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_ctx_step_id: ContextVar[str | None] = ContextVar("step_id", default=None)


def _to_micro(usd: float) -> int:
    """Convert USD float to integer microusd (1 USD = 1,000,000)."""
    return round(usd * _MICRO)


def _from_micro(micro: int) -> float:
    """Convert integer microusd to USD float."""
    return micro / _MICRO


class RedisArbiter:
    """Multi-process budget arbiter using Redis for shared state.

    Uses Lua scripts for atomic reserve/settle operations.
    Falls back to ProportionalArbiter on Redis failure.

    Context injection via set_arbitration_context() uses contextvars,
    safe for concurrent async/threaded callers.

    Contract: Unsettled reservations are a budget leak. TTL expiry is
    a safety valve, not a refund mechanism. Call settle() promptly.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        budget_scope: str = "default",
        total_budget_usd: float = 100.0,
        reservation_ttl_s: int = 300,
        fallback: ProportionalArbiter | None = None,
    ) -> None:
        try:
            import redis as redis_lib
        except ImportError as e:
            raise ImportError(
                "redis package required for RedisArbiter. "
                "Install with: pip install veronica[redis]"
            ) from e

        self._redis = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        self._redis_lib = redis_lib
        self._scope = budget_scope
        self._total_budget_micro = _to_micro(total_budget_usd)
        self._ttl = reservation_ttl_s
        self._fallback = fallback or ProportionalArbiter()

        self._reserve_script = self._redis.register_script(LUA_RESERVE)
        self._settle_script = self._redis.register_script(LUA_SETTLE)

    def set_arbitration_context(
        self,
        request_id: str,
        step_id: str,
    ) -> None:
        """Inject request/step identity for idempotent reservation."""
        _ctx_request_id.set(request_id)
        _ctx_step_id.set(step_id)

    def arbitrate(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
    ) -> Mapping[str, PolicyConfig]:
        """Reserve budget via Redis. Falls back to local arbiter on failure."""
        request_id = _ctx_request_id.get()
        step_id = _ctx_step_id.get()

        if request_id is None or step_id is None:
            return self._fallback.arbitrate(desires, budget_remaining_usd)

        try:
            return self._arbitrate_redis(
                desires, budget_remaining_usd, request_id, step_id,
            )
        except self._redis_lib.RedisError:
            logger.warning("Redis unavailable, falling back to local arbiter")
            return self._fallback.arbitrate(desires, budget_remaining_usd)

    def settle(
        self,
        request_id: str,
        step_id: str,
        actual_cost_usd: float,
    ) -> None:
        """Settle reservation: refund surplus or deduct extra."""
        remaining_key = f"veronica:{self._scope}:budget:remaining"
        alloc_key = f"veronica:{self._scope}:alloc:{request_id}:{step_id}"
        actual_micro = _to_micro(actual_cost_usd)

        try:
            self._settle_script(
                keys=[remaining_key, alloc_key],
                args=[str(actual_micro)],
            )
        except self._redis_lib.RedisError:
            logger.warning(
                "Redis settle failed for %s:%s", request_id, step_id,
            )

    def _arbitrate_redis(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
        request_id: str,
        step_id: str,
    ) -> Mapping[str, PolicyConfig]:
        if not desires:
            return {}

        desire = desires[0]
        reserve_micro = _to_micro(desire.ceiling_usd)

        remaining_key = f"veronica:{self._scope}:budget:remaining"
        alloc_key = f"veronica:{self._scope}:alloc:{request_id}:{step_id}"

        alloc_json = json.dumps({
            "status": "reserved",
            "chain_id": desire.chain_id,
            "total_reserved_micro": reserve_micro,
            "ceiling_usd": desire.ceiling_usd,
            "reserved_at": time.time(),
        })

        result = self._reserve_script(
            keys=[remaining_key, alloc_key],
            args=[
                alloc_json,
                str(reserve_micro),
                str(self._ttl),
                str(self._total_budget_micro),
            ],
        )

        parsed = json.loads(result)

        if parsed["status"] == "insufficient":
            ceiling = _from_micro(int(parsed["remaining"]))
        else:
            ceiling = desire.ceiling_usd

        now = time.time()
        return {
            desire.chain_id: PolicyConfig(
                chain_id=desire.chain_id,
                ceiling_usd=ceiling,
                ceiling_steps=desire.ceiling_steps,
                ceiling_tokens_out=desire.ceiling_tokens_out,
                on_exceed=desire.on_exceed,
                fallback_model=desire.fallback_model,
                timeout_ms=desire.timeout_ms,
                priority=desire.priority,
                issued_at=now,
                planner_version="0.3.0",
            ),
        }
```

**Step 2: Verify import works**

Run: `cd D:/work/Projects/veronica && python -c "from veronica.redis_arbiter import RedisArbiter, _to_micro, _from_micro; print('OK')"`
Expected: `OK` (no import errors; Redis connection not attempted until instantiation)

**Step 3: Commit**

```bash
git add src/veronica/redis_arbiter.py
git commit -m "feat: add RedisArbiter with 2-phase reserve/settle"
```

---

### Task 4: Unit Tests (Layer 1 -- fakeredis)

**Files:**
- Create: `tests/test_redis_arbiter.py`

**Step 1: Write the 9 unit tests**

```python
# tests/test_redis_arbiter.py
"""Tests for RedisArbiter -- multi-process budget allocation."""
from __future__ import annotations

import time

import fakeredis
import pytest

from veronica.redis_arbiter import RedisArbiter, _from_micro, _to_micro
from veronica.types import DesiredPolicy


def _desire(
    chain_id: str = "c1",
    ceiling_usd: float = 10.0,
    priority: int = 50,
) -> DesiredPolicy:
    return DesiredPolicy(
        chain_id=chain_id,
        ceiling_usd=ceiling_usd,
        ceiling_steps=100,
        ceiling_tokens_out=50_000,
        on_exceed="halt",
        fallback_model=None,
        timeout_ms=30_000,
        priority=priority,
    )


@pytest.fixture()
def arbiter():
    """RedisArbiter backed by fakeredis."""
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    arb = RedisArbiter.__new__(RedisArbiter)
    import redis as redis_lib
    arb._redis = fake_redis
    arb._redis_lib = redis_lib
    arb._scope = "test"
    arb._total_budget_micro = _to_micro(100.0)
    arb._ttl = 300
    from veronica.proportional_arbiter import ProportionalArbiter
    arb._fallback = ProportionalArbiter()
    from veronica._redis_scripts import LUA_RESERVE, LUA_SETTLE
    arb._reserve_script = fake_redis.register_script(LUA_RESERVE)
    arb._settle_script = fake_redis.register_script(LUA_SETTLE)
    return arb


class TestMicrousd:
    def test_to_micro(self) -> None:
        assert _to_micro(0.01) == 10_000

    def test_from_micro(self) -> None:
        assert _from_micro(10_000) == 0.01

    def test_roundtrip(self) -> None:
        for usd in [0.0, 0.01, 1.0, 99.999999, 100.0]:
            assert abs(_from_micro(_to_micro(usd)) - usd) < 1e-6


class TestReserve:
    def test_reserve_basic(self, arbiter: RedisArbiter) -> None:
        """Reserve 10 USD from 100 USD budget."""
        arbiter.set_arbitration_context("r1", "s1")
        configs = arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)
        assert "c1" in configs
        assert configs["c1"].ceiling_usd == 10.0

        # Check Redis remaining: 100 - 10 = 90 USD
        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(90.0)

    def test_reserve_idempotent(self, arbiter: RedisArbiter) -> None:
        """Same request_id + step_id called twice -> budget deducted once."""
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(90.0)  # deducted once, not twice

    def test_reserve_insufficient(self, arbiter: RedisArbiter) -> None:
        """Reserve more than available -> returns remaining amount."""
        arbiter.set_arbitration_context("r1", "s1")
        configs = arbiter.arbitrate([_desire(ceiling_usd=200.0)], 100.0)
        assert configs["c1"].ceiling_usd == 100.0  # gets remaining

    def test_fallback_no_context(self, arbiter: RedisArbiter) -> None:
        """arbitrate() without set_arbitration_context() uses fallback."""
        # Don't call set_arbitration_context
        configs = arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)
        assert "c1" in configs
        # Redis should not have been touched
        assert arbiter._redis.get("veronica:test:budget:remaining") is None


class TestSettle:
    def test_settle_refund(self, arbiter: RedisArbiter) -> None:
        """Reserve 10, settle 7 -> remaining increases by 3."""
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        arbiter.settle("r1", "s1", actual_cost_usd=7.0)

        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(93.0)  # 100 - 10 + 3 = 93

    def test_settle_extra_deduction(self, arbiter: RedisArbiter) -> None:
        """Reserve 5, settle 8 -> remaining decreases by 3."""
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=5.0)], 100.0)

        arbiter.settle("r1", "s1", actual_cost_usd=8.0)

        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(92.0)  # 100 - 5 - 3 = 92

    def test_settle_expired(self, arbiter: RedisArbiter) -> None:
        """TTL-expired reservation: settle deducts actual cost."""
        arbiter._ttl = 1  # 1 second TTL
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        # remaining = 90 after reserve
        # Simulate TTL expiry by deleting the alloc key
        arbiter._redis.delete("veronica:test:alloc:r1:s1")

        arbiter.settle("r1", "s1", actual_cost_usd=7.0)

        # Expired path: deducts actual (7) from remaining (90) = 83
        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(83.0)

    def test_settle_alloc_key_cleaned(self, arbiter: RedisArbiter) -> None:
        """After settle, alloc key is deleted."""
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        assert arbiter._redis.get("veronica:test:alloc:r1:s1") is not None
        arbiter.settle("r1", "s1", actual_cost_usd=10.0)
        assert arbiter._redis.get("veronica:test:alloc:r1:s1") is None
```

**Step 2: Run tests to verify they pass**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_redis_arbiter.py -v`
Expected: 9 tests PASS

**Step 3: Commit**

```bash
git add tests/test_redis_arbiter.py
git commit -m "test: add RedisArbiter unit tests (9 tests, fakeredis)"
```

---

### Task 5: os.py Wiring (6 lines)

**Files:**
- Modify: `src/veronica/os.py:158,254` (add hasattr guards)

**Step 1: Add set_arbitration_context before arbiter call**

In `src/veronica/os.py`, in `before_step()`, insert BEFORE the `# 5. Arbiter` section (before line 158):

```python
        # 4b. Inject arbitration context for RedisArbiter idempotency
        if hasattr(self._arbiter, "set_arbitration_context"):
            self._arbiter.set_arbitration_context(
                request_id=intent.request_id,
                step_id=intent.step_id,
            )
```

**Step 2: Add settle after successful commit**

In `src/veronica/os.py`, in `after_step()`, insert AFTER `self._store.commit(...)` (after line 254) and BEFORE `# 6. EventEmitter`:

```python
        # 5b. Settle reservation with actual cost
        if hasattr(self._arbiter, "settle"):
            self._arbiter.settle(
                request_id=handle.intent.request_id,
                step_id=handle.intent.step_id,
                actual_cost_usd=outcome.cost_usd,
            )
```

**Step 3: Run existing tests to verify no regression**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ -v`
Expected: All existing tests PASS (hasattr guards are no-ops for PassthroughArbiter and ProportionalArbiter)

**Step 4: Commit**

```bash
git add src/veronica/os.py
git commit -m "feat: wire set_arbitration_context and settle in os.py"
```

---

### Task 6: Atomicity Tests (Layer 2 -- threading)

**Files:**
- Modify: `tests/test_redis_arbiter.py` (add 2 threading tests)

**Step 1: Add concurrent reserve test**

Append to `tests/test_redis_arbiter.py`:

```python
import threading


class TestAtomicity:
    def test_concurrent_reserve(self, arbiter: RedisArbiter) -> None:
        """10 threads each reserve 10 USD from 100 USD budget.

        All 10 should succeed (total = 100), remaining = 0.
        """
        results: list[float] = []
        errors: list[Exception] = []

        def reserve(i: int) -> None:
            try:
                arbiter.set_arbitration_context(f"r{i}", f"s{i}")
                configs = arbiter.arbitrate(
                    [_desire(chain_id=f"c{i}", ceiling_usd=10.0)], 100.0,
                )
                results.append(configs[f"c{i}"].ceiling_usd)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reserve, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == 0

    def test_concurrent_reserve_and_settle(self, arbiter: RedisArbiter) -> None:
        """5 reserves followed by 5 settles. Budget is consistent."""
        # Sequential reserves first (to have alloc keys)
        for i in range(5):
            arbiter.set_arbitration_context(f"r{i}", f"s{i}")
            arbiter.arbitrate(
                [_desire(chain_id=f"c{i}", ceiling_usd=10.0)], 100.0,
            )

        # remaining = 50 after 5 reserves of 10 each
        remaining_before = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining_before == _to_micro(50.0)

        # Concurrent settles: each actual = 8 (refund 2 each)
        errors: list[Exception] = []

        def settle(i: int) -> None:
            try:
                arbiter.settle(f"r{i}", f"s{i}", actual_cost_usd=8.0)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=settle, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # 50 + 5*(10-8) = 50 + 10 = 60
        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(60.0)
```

**Step 2: Run all RedisArbiter tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_redis_arbiter.py -v`
Expected: 11 tests PASS (9 unit + 2 atomicity)

**Step 3: Commit**

```bash
git add tests/test_redis_arbiter.py
git commit -m "test: add RedisArbiter atomicity tests (concurrent reserve/settle)"
```

---

### Task 7: Integration Tests (Layer 3 -- with os.py)

**Files:**
- Modify: `tests/test_redis_arbiter.py` (add 2 integration tests)

**Step 1: Add integration tests**

Append to `tests/test_redis_arbiter.py`:

```python
from datetime import datetime, timezone

from veronica_core.containment.execution_context import ContextSnapshot, NodeRecord

from veronica.adaptive_planner import AdaptivePlanner
from veronica.buffered_emitter import BufferedEmitter
from veronica.file_store import FileStore
from veronica.history_analyzer import HistoryAnalyzer
from veronica.os import VeronicaOS
from veronica.regression_cost_model import RegressionCostModel
from veronica.types import StepIntent


def _intent(
    step_id: str = "s1",
    request_id: str = "r1",
    chain_id: str = "c1",
    model: str = "gpt-4",
) -> StepIntent:
    return StepIntent(
        step_id=step_id, request_id=request_id, chain_id=chain_id,
        kind="llm", model=model, tool_name=None,
        timeout_ms=30_000, metadata={},
    )


def _snapshot(
    chain_id: str = "c1",
    cost: float = 0.01,
    status: str = "ok",
) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1", parent_id=None, kind="llm",
        operation_name="test_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status=status, cost_usd=cost, retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id, request_id="r1", step_count=1,
        cost_usd_accumulated=cost, retries_used=0,
        aborted=False, abort_reason=None,
        elapsed_ms=100.0, nodes=[node], events=[],
    )


class TestIntegration:
    def test_full_pipeline_with_redis_arbiter(
        self, arbiter: RedisArbiter, tmp_path,
    ) -> None:
        """VeronicaOS with RedisArbiter: before_step + after_step."""
        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=arbiter,
            store=FileStore(data_dir=str(tmp_path)),
            request_budget_usd=100.0,
        )

        handle = vos.before_step(_intent(step_id="s1", request_id="r1"))
        assert handle.policy.ceiling_usd > 0
        vos.after_step(handle, _snapshot(cost=0.01))

        # Verify Redis budget was updated
        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining < _to_micro(100.0)

    def test_backward_compat_default_os(self) -> None:
        """Default VeronicaOS (no RedisArbiter) still works."""
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert handle.policy.ceiling_usd > 0
        vos.after_step(handle, _snapshot())
```

**Step 2: Run all tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ -v`
Expected: All tests PASS (existing + 13 new RedisArbiter tests)

**Step 3: Commit**

```bash
git add tests/test_redis_arbiter.py
git commit -m "test: add RedisArbiter integration tests with os.py pipeline"
```

---

### Task 8: Export + Version

**Files:**
- Modify: `src/veronica/__init__.py:4-9,27-48` (add RedisArbiter export)
- Modify: `pyproject.toml:7` (version bump)

**Step 1: Add RedisArbiter to __init__.py**

Add import (lazy, since redis is optional):

In `src/veronica/__init__.py`, add to the import block:

```python
from veronica.redis_arbiter import RedisArbiter
```

And add `"RedisArbiter"` to `__all__` after `"ProportionalArbiter"`.

**Step 2: Update version to 0.3.0**

In `src/veronica/__init__.py`, change `__version__ = "0.2.1"` to `__version__ = "0.3.0"`.

In `pyproject.toml`, change `version = "0.2.1"` to `version = "0.3.0"`.

**Step 3: Verify import works**

Run: `cd D:/work/Projects/veronica && python -c "import veronica; print(veronica.__version__, hasattr(veronica, 'RedisArbiter'))"`
Expected: `0.3.0 True`

**Step 4: Commit**

```bash
git add src/veronica/__init__.py pyproject.toml
git commit -m "chore: export RedisArbiter, bump version to 0.3.0"
```

---

### Task 9: Final Verification + Tag

**Files:** None (verification only)

**Step 1: Run full test suite**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS. No failures, no warnings.

**Step 2: Run with coverage**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ --cov=veronica --cov-report=term-missing`
Expected: Coverage >= 80% (coverage target from pyproject.toml)

**Step 3: Verify backward compatibility**

Run:
```bash
python -c "
from veronica import VeronicaOS
vos = VeronicaOS()
print('Default OS:', type(vos._arbiter).__name__)
"
```
Expected: `Default OS: PassthroughArbiter`

**Step 4: Tag and push**

```bash
git tag v0.3.0
git push origin main --tags
```

---

## Summary

| Task | Description | Files | Tests |
|------|-------------|-------|-------|
| 1 | Lua script constants | `_redis_scripts.py` (new) | -- |
| 2 | pyproject.toml deps | `pyproject.toml` (mod) | -- |
| 3 | RedisArbiter core | `redis_arbiter.py` (new) | -- |
| 4 | Unit tests (9) | `test_redis_arbiter.py` (new) | 9 |
| 5 | os.py wiring (6 lines) | `os.py` (mod) | regression |
| 6 | Atomicity tests (2) | `test_redis_arbiter.py` (mod) | 2 |
| 7 | Integration tests (2) | `test_redis_arbiter.py` (mod) | 2 |
| 8 | Export + version | `__init__.py`, `pyproject.toml` (mod) | -- |
| 9 | Final verification + tag | -- | full suite |

**Total new tests:** 13
**Total new source files:** 2 (`_redis_scripts.py`, `redis_arbiter.py`)
**Total modified files:** 3 (`os.py`, `__init__.py`, `pyproject.toml`)
**Protocol changes:** None
