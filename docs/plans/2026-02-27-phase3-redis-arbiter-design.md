# Phase 3: RedisArbiter -- Multi-Process Budget Allocation

**Goal:** Enable multiple VeronicaOS processes to share budget allocation via Redis, without changing Protocol interfaces.

**Approach:** New `RedisArbiter` implementation of `ArbiterProtocol`. Lua scripts for atomic reserve/settle. Fallback to local `ProportionalArbiter` on Redis failure.

**Scope:**
1. RedisArbiter with 2-phase reserve/settle
2. os.py wiring (6 lines, hasattr guards)
3. Idempotent allocation via `request_id + step_id` keys
4. Integer arithmetic (microusd) for all money values

**Protocol changes:** None.

---

## Architecture

### 2-Phase Model

```
before_step():
  os.py -> set_arbitration_context(request_id, step_id)
  os.py -> arbiter.arbitrate(desires, remaining)
           └── Lua RESERVE: deduct estimated cost from shared budget
               └── Idempotent: alloc key exists? return cached result

after_step():
  os.py -> store.commit(...)  # must succeed first
  os.py -> arbiter.settle(request_id, step_id, actual_cost_usd)
           └── Lua SETTLE: refund (reserved - actual) or deduct extra
```

### Redis Key Structure

```
veronica:{scope}:budget:remaining              -> str(int microusd)
veronica:{scope}:budget:total                   -> str(int microusd)
veronica:{scope}:alloc:{request_id}:{step_id}   -> JSON (with TTL)
```

- `scope` is configurable per RedisArbiter instance (default: `"default"`)
- `remaining` is the shared budget counter
- `alloc` keys are per-step allocation records for idempotency and settle

### Allocation Record (JSON stored in alloc key)

```json
{
  "status": "reserved",
  "chain_id": "c1",
  "total_reserved_micro": 10000,
  "ceiling_usd": 0.01,
  "reserved_at": 1709000000.0
}
```

---

## RedisArbiter Implementation

### Constructor

```python
class RedisArbiter:
    """Multi-process budget arbiter using Redis for shared state.

    Uses Lua scripts for atomic reserve/settle operations.
    Falls back to ProportionalArbiter on Redis failure.

    Re-entrancy: set_arbitration_context() uses contextvars,
    safe for concurrent async/threaded callers.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        budget_scope: str = "default",
        total_budget_usd: float = 100.0,
        reservation_ttl_s: int = 300,
        fallback: ArbiterProtocol | None = None,
    ) -> None:
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._scope = budget_scope
        self._total_budget_micro = _to_micro(total_budget_usd)
        self._ttl = reservation_ttl_s
        self._fallback = fallback or ProportionalArbiter()

        # Register Lua scripts
        self._reserve_script = self._redis.register_script(_LUA_RESERVE)
        self._settle_script = self._redis.register_script(_LUA_SETTLE)
```

### Context Injection (contextvars)

```python
from contextvars import ContextVar

_ctx_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_ctx_step_id: ContextVar[str | None] = ContextVar("request_id", default=None)

class RedisArbiter:
    def set_arbitration_context(
        self,
        request_id: str,
        step_id: str,
    ) -> None:
        _ctx_request_id.set(request_id)
        _ctx_step_id.set(step_id)
```

### Integer Arithmetic (microusd)

```python
_MICRO = 1_000_000

def _to_micro(usd: float) -> int:
    return round(usd * _MICRO)

def _from_micro(micro: int) -> float:
    return micro / _MICRO
```

All Redis values stored as integer strings. No floating-point rounding in Redis.

### arbitrate()

```python
def arbitrate(
    self,
    desires: Sequence[DesiredPolicy],
    budget_remaining_usd: float,
) -> Mapping[str, PolicyConfig]:
    request_id = _ctx_request_id.get()
    step_id = _ctx_step_id.get()

    if request_id is None or step_id is None:
        # No context set -- fall back to local arbiter
        return self._fallback.arbitrate(desires, budget_remaining_usd)

    try:
        return self._arbitrate_redis(desires, budget_remaining_usd, request_id, step_id)
    except redis.RedisError:
        logger.warning("Redis unavailable, falling back to local arbiter")
        return self._fallback.arbitrate(desires, budget_remaining_usd)
```

### _arbitrate_redis()

```python
def _arbitrate_redis(
    self,
    desires: Sequence[DesiredPolicy],
    budget_remaining_usd: float,
    request_id: str,
    step_id: str,
) -> Mapping[str, PolicyConfig]:
    if not desires:
        return {}

    # Use first desire (single-chain common case)
    desire = desires[0]
    reserve_micro = _to_micro(desire.ceiling_usd)

    remaining_key = f"veronica:{self._scope}:budget:remaining"
    alloc_key = f"veronica:{self._scope}:alloc:{request_id}:{step_id}"

    alloc_json = cjson.dumps({
        "status": "reserved",
        "chain_id": desire.chain_id,
        "total_reserved_micro": reserve_micro,
        "ceiling_usd": desire.ceiling_usd,
        "reserved_at": time.time(),
    })

    result = self._reserve_script(
        keys=[remaining_key, alloc_key],
        args=[alloc_json, str(reserve_micro), str(self._ttl), str(self._total_budget_micro)],
    )

    parsed = json.loads(result)

    if parsed["status"] == "insufficient":
        # Not enough budget -- allocate whatever remains
        actual_micro = parsed["remaining"]
        ceiling = _from_micro(actual_micro)
    elif parsed["status"] == "reserved" or parsed["status"] == "reused":
        ceiling = desire.ceiling_usd
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

### settle()

```python
def settle(
    self,
    request_id: str,
    step_id: str,
    actual_cost_usd: float,
) -> None:
    remaining_key = f"veronica:{self._scope}:budget:remaining"
    alloc_key = f"veronica:{self._scope}:alloc:{request_id}:{step_id}"
    actual_micro = _to_micro(actual_cost_usd)

    try:
        self._settle_script(
            keys=[remaining_key, alloc_key],
            args=[str(actual_micro)],
        )
    except redis.RedisError:
        logger.warning("Redis settle failed for %s:%s", request_id, step_id)
```

---

## Lua Scripts

### Reserve Script

```lua
-- KEYS[1] = remaining_key
-- KEYS[2] = alloc_key
-- ARGV[1] = alloc_json (full allocation record)
-- ARGV[2] = reserve_micro (amount to deduct)
-- ARGV[3] = ttl_seconds
-- ARGV[4] = total_budget_micro (initial value if key missing)

-- Idempotency check
local existing = redis.call('GET', KEYS[2])
if existing then
    return existing  -- already reserved (has status field)
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
```

### Settle Script

```lua
-- KEYS[1] = remaining_key
-- KEYS[2] = alloc_key
-- ARGV[1] = actual_cost_micro

local alloc_json = redis.call('GET', KEYS[2])

if not alloc_json then
    -- TTL expired: allocation record gone.
    -- Still deduct actual cost (safe side -- see Implementation Note 3).
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
local diff = reserved - actual  -- positive = refund, negative = extra deduction

local remaining = tonumber(redis.call('GET', KEYS[1]))

if diff > 0 then
    -- Refund: actual < reserved
    redis.call('SET', KEYS[1], tostring(remaining + diff))
elseif diff < 0 then
    -- Extra deduction: actual > reserved
    local new_remaining = remaining + diff
    if new_remaining < 0 then new_remaining = 0 end
    redis.call('SET', KEYS[1], tostring(new_remaining))
end
-- diff == 0: no change needed

redis.call('DEL', KEYS[2])
return cjson.encode({status = "settled", diff = diff})
```

---

## os.py Changes (6 lines)

### before_step() -- before arbiter call (~line 158)

```python
# Inject arbitration context for RedisArbiter idempotency
if hasattr(self._arbiter, "set_arbitration_context"):
    self._arbiter.set_arbitration_context(
        request_id=intent.request_id,
        step_id=intent.step_id,
    )
```

### after_step() -- after successful commit (~line 254)

```python
# Settle reservation with actual cost
if hasattr(self._arbiter, "settle"):
    self._arbiter.settle(
        request_id=handle.intent.request_id,
        step_id=handle.intent.step_id,
        actual_cost_usd=outcome.cost_usd,
    )
```

**settle is called ONLY after successful commit.** If commit fails, the reservation remains and eventually expires via TTL. This is the correct safe-side behavior: budget depletes faster on failures, which is conservative.

---

## Fallback Behavior

| Condition | Behavior |
|-----------|----------|
| `set_arbitration_context()` not called | Falls back to `ProportionalArbiter` (local) |
| Redis connection fails | Falls back to `ProportionalArbiter` (local) |
| `settle()` Redis error | Warning logged, reservation expires via TTL |
| TTL expires before settle | Budget lost (safe side -- see Note 3) |
| Same `request_id + step_id` called twice | Returns cached allocation (idempotent) |

---

## pyproject.toml Changes

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

---

## Tests (3-Layer Strategy)

### Layer 1: Unit Tests (fakeredis)

1. **Reserve basic**: Reserve 10 USD from 100 USD budget -> remaining = 90 USD.
2. **Reserve idempotent**: Same request_id + step_id twice -> same result, budget deducted once.
3. **Reserve insufficient**: Reserve 200 USD from 100 USD budget -> status = "insufficient".
4. **Settle refund**: Reserve 10 USD, settle 7 USD -> remaining increases by 3 USD.
5. **Settle extra deduction**: Reserve 5 USD, settle 8 USD -> remaining decreases by 3 USD.
6. **Settle expired**: Reserve with TTL=1, sleep(2), settle -> deducts actual cost, status = "expired".
7. **Fallback on no context**: arbitrate() without set_arbitration_context() -> uses ProportionalArbiter.
8. **Fallback on Redis error**: Redis connection fails -> uses ProportionalArbiter.
9. **Microusd roundtrip**: _to_micro(0.01) == 10_000, _from_micro(10_000) == 0.01.

### Layer 2: Atomicity Tests (fakeredis, threading)

10. **Concurrent reserve**: 10 threads reserve 10 USD each from 100 USD. Exactly 10 succeed, remaining = 0.
11. **Concurrent reserve + settle**: 5 threads reserve, 5 settle. No double-deduction, budget consistent.

### Layer 3: Integration Tests (with os.py)

12. **Full pipeline**: VeronicaOS with RedisArbiter -> before_step + after_step -> budget correctly tracked.
13. **Headroom injected**: os.py injects budget context into FileStore, settle adjusts Redis remaining.

---

## Implementation Notes (Nails)

### Note 1: ContextVar Typing

```python
_ctx_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_ctx_step_id: ContextVar[str | None] = ContextVar("step_id", default=None)
```

`ContextVar[str]` with `.get(None)` does not type-check. The type parameter must be `str | None` with `default=None`. This ensures `mypy` and `pyright` accept `.get()` returning `None`.

### Note 2: Uniform Lua Return Format

All Lua scripts return JSON with a `status` field:
- Reserve success: `{"status": "reserved", ...}`
- Reserve reused (idempotent): `{"status": "reserved", ...}` (cached alloc_json)
- Reserve insufficient: `{"status": "insufficient", "remaining": N, "requested": M}`
- Settle success: `{"status": "settled", "diff": N}`
- Settle expired: `{"status": "expired", "deducted": N}`

Python code always does `json.loads(result)` and switches on `parsed["status"]`. No special-casing of raw strings vs JSON.

### Note 3: Expired Settle Deducts Actual Cost

When a reservation TTL expires before `settle()` is called:

1. The `alloc:{request_id}:{step_id}` key is gone.
2. The reserved amount was already deducted from `remaining` during reserve.
3. `settle()` must still deduct `actual_cost` from `remaining` (because the TTL expiry already "refunded" the reserved amount by deleting the record).

Wait -- that's not right. Let me clarify the actual flow:

1. Reserve: `remaining -= reserved`. Alloc key created with TTL.
2. TTL expires: alloc key deleted. **remaining is NOT changed** (Redis TTL only deletes the key, it doesn't refund).
3. Settle (expired path): The reserved amount is already gone from remaining. Now the actual cost may differ:
   - If `actual < reserved`: budget was over-deducted by `reserved - actual`. But we can't know `reserved` because the key is gone. So we accept the loss. This is the safety penalty.
   - If `actual > reserved`: we need to deduct the additional `actual - reserved`. But again, we don't know `reserved`.

**Corrected behavior**: On expired settle, the reserved amount is already deducted and lost. We only need to handle the case where `actual > reserved` -- but since we don't know `reserved`, we take the safe-side approach: deduct `actual` as if no reservation existed, accepting potential double-deduction. This means the budget depletes faster, which is the documented safety penalty.

**Contract**: Unsettled reservations are a budget leak. The TTL expiry is a safety valve, not a refund mechanism. Applications must call `settle()` promptly. If they don't, budget pressure increases -- this is intentional.

### Note 4: settle() Only After Successful Commit

`os.py` calls `settle()` AFTER `store.commit()` succeeds. If commit raises an exception, settle is skipped, and the reservation remains until TTL expiry. This ensures the Store and Redis stay consistent: a committed step is always settled, and an uncommitted step eventually loses its reservation (safe side).

---

## Files Summary

| File | Change |
|------|--------|
| `src/veronica/redis_arbiter.py` | New: RedisArbiter class |
| `src/veronica/_redis_scripts.py` | New: Lua script constants |
| `src/veronica/os.py:158,254` | 6 lines: set_arbitration_context + settle |
| `src/veronica/__init__.py` | Export RedisArbiter |
| `pyproject.toml` | Add fakeredis[lua] dev dep, redis optional dep |
| `tests/test_redis_arbiter.py` | New: 13 tests (unit + atomicity + integration) |

**Protocol changes:** None.
**os.py pipeline structure:** Unchanged (2 `hasattr` calls added).
