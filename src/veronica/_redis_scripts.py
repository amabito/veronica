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
