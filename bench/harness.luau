-- Shared helpers: limits, timed repeats, the counting bridge, upstream's seeded RNG, and a JSON encoder.

local M = {}

-- DEADLINE: the most one timed run may take; a run past it is a DNF, charged exactly this in the geomean.
-- BUDGET: timed seconds a bench spends on its repeats before stopping early (always at least one run).
-- WALL: wall-clock seconds its repeats may take in all, untimed rebuilds included.
-- Both stop a slow-but-finishing core with fewer repeats rather than at benchmark.py's process cap.
M.DEADLINE = 8
M.BUDGET = 10
M.WALL = 30

-- One full collection. Upstream calls gc() twice to settle V8 finalizers; Luau has none to settle.
function M.gc()
	collectgarbage("collect")
end

-- A failure raised by the harness itself, carrying its row status.
local Failure = {
	__tostring = function(f)
		return f.message
	end,
}

function M.fail(status: "timeout" | "wrong", message: string)
	error(setmetatable({ status = status, message = message }, Failure))
end

function M.checkDeadline(secs: number, what: string)
	if secs > M.DEADLINE then
		M.fail("timeout", string.format("deadline exceeded: %.1fs %s", secs, what))
	end
end

-- Up to `repeats` runs of fn (which times itself and returns seconds), each from a collected heap, stopping
-- early at `budget` timed seconds (default BUDGET) or at WALL. Returns the fastest time, total time, and run count.
function M.runs(repeats: number, fn: () -> number, budget: number?): (number, number, number)
	local best, spent, n = math.huge, 0, 0
	local limit = budget or M.BUDGET
	local stop = os.clock() + M.WALL
	for _ = 1, repeats do
		M.gc()
		local secs = fn()
		M.checkDeadline(secs, "run")
		if secs < best then
			best = secs
		end
		spent += secs
		n += 1
		if spent > limit or os.clock() > stop then
			break
		end
	end
	return best, spent, n
end

-- Upstream's fastestTest.
function M.fastest(repeats: number, fn: () -> number): number
	return (M.runs(repeats, fn))
end

-- Upstream's cellx sums its runs; the mean ranks identically.
function M.mean(repeats: number, fn: () -> number): number
	local _, spent, n = M.runs(repeats, fn)
	return spent / n
end

-- Runs body(counted, reset) untimed on a bridge that counts every computed and effect body run, then cleans
-- up. Returns the computed and effect runs since the last reset(). Timed code never goes through this.
function M.count(bridge, body: (any, () -> ()) -> ()): (number, number)
	local computed, effect = 0, 0
	local counted = setmetatable({
		computed = function(fn)
			return bridge.computed(function()
				computed += 1
				return fn()
			end)
		end,
		effect = function(fn)
			bridge.effect(function()
				effect += 1
				fn()
			end)
		end,
	}, { __index = bridge })
	body(counted, function()
		computed, effect = 0, 0
	end)
	bridge.cleanup()
	return computed, effect
end

-- Upstream's RNG (util/pseudoRandom.ts): sfc32 seeded by xmur3a(seed), floats in [0, 1), ported bit-for-bit
-- so the dynamic graphs are the ones the reference builds.

local band, bxor, rshift, lshift, lrotate = bit32.band, bit32.bxor, bit32.rshift, bit32.lshift, bit32.lrotate
local M32 = 0x100000000

-- Math.imul, read as unsigned
local function imul(a: number, b: number): number
	local ah, al = rshift(a, 16), band(a, 0xffff)
	local bh, bl = rshift(b, 16), band(b, 0xffff)
	return (al * bl + lshift(band(ah * bl + al * bh, 0xffff), 16)) % M32
end

local function xmur3a(str: string): () -> number
	local h = 2166136261
	for i = 1, #str do
		local k = imul(string.byte(str, i), 3432918353)
		h = bxor(h, imul(lrotate(k, 15), 461845907))
		h = (imul(lrotate(h, 13), 5) + 3864292196) % M32
	end
	h = bxor(h, #str)
	return function(): number
		h = imul(bxor(h, rshift(h, 16)), 2246822507)
		h = imul(bxor(h, rshift(h, 13)), 3266489909)
		h = bxor(h, rshift(h, 16))
		return h
	end
end

function M.rng(): () -> number
	local hash = xmur3a("seed")
	local a, b, c, d = hash(), hash(), hash(), hash()
	return function(): number
		local t = (a + b) % M32
		a = bxor(b, rshift(b, 9))
		b = (c * 9) % M32
		c = lrotate(c, 21)
		d = (d + 1) % M32
		t = (t + d) % M32
		c = (c + t) % M32
		return t / M32
	end
end

-- Minimal JSON encoder for what main.luau hands back. Empty tables encode as [].
function M.json(v: any): string
	local t = type(v)
	if t == "string" then
		return '"' .. v:gsub('[%c"\\]', function(c)
			return string.format("\\u%04x", c:byte())
		end) .. '"'
	elseif t == "number" then
		return string.format("%.17g", v)
	elseif t == "boolean" then
		return tostring(v)
	elseif t == "table" then
		local parts = {}
		if #v > 0 or next(v) == nil then
			for _, x in v do
				table.insert(parts, M.json(x))
			end
			return "[" .. table.concat(parts, ",") .. "]"
		end
		for k, x in v do
			table.insert(parts, M.json(tostring(k)) .. ":" .. M.json(x))
		end
		return "{" .. table.concat(parts, ",") .. "}"
	end
	return "null"
end

return M
