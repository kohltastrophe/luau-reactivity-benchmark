-- Port of the kairo suite from milomg/js-reactivity-benchmark (packages/core/src/benches/kairo/*).
--
-- Each case builds upstream's graph and returns its iterator, with upstream's bodies and value assertions
-- as-is, except that its exact effect count is a floor: a framework that isn't glitch-free legitimately
-- re-runs more, while an adapter whose effects never re-run (which value asserts alone can't see, since reads
-- pull lazily) still fails. The cases upstream doesn't count (mux, mol) carry an `effects` floor checked
-- through the counting bridge.

local harness = require("../harness")

local function expect(cond: boolean, what: string)
	if not cond then
		harness.fail("wrong", what)
	end
end

local function busy()
	local a = 0
	for _ = 1, 100 do
		a += 1
	end
end

local function avoidablePropagation(bridge): () -> ()
	local head = bridge.signal(0)
	local computed1 = bridge.computed(function()
		return head.read()
	end)
	local computed2 = bridge.computed(function()
		computed1.read()
		return 0
	end)
	local computed3 = bridge.computed(function()
		busy() -- heavy computation
		return computed2.read() + 1
	end)
	local computed4 = bridge.computed(function()
		return computed3.read() + 2
	end)
	local computed5 = bridge.computed(function()
		return computed4.read() + 3
	end)
	bridge.effect(function()
		computed5.read()
		busy() -- heavy side effect
	end)
	return function()
		bridge.withBatch(function()
			head.write(1)
		end)
		expect(computed5.read() == 6, "avoidable baseline")
		for i = 0, 999 do
			bridge.withBatch(function()
				head.write(i)
			end)
			expect(computed5.read() == 6, "avoidable")
		end
	end
end

local function broadPropagation(bridge): () -> ()
	local head = bridge.signal(0)
	local last = head
	local callCounter = { count = 0 }
	for i = 0, 49 do
		local current = bridge.computed(function()
			return head.read() + i
		end)
		local current2 = bridge.computed(function()
			return current.read() + 1
		end)
		bridge.effect(function()
			current2.read()
			callCounter.count += 1
		end)
		last = current2
	end
	return function()
		bridge.withBatch(function()
			head.write(1)
		end)
		local atleast = 50 * 50
		callCounter.count = 0
		for i = 0, 49 do
			bridge.withBatch(function()
				head.write(i)
			end)
			expect(last.read() == i + 50, "broad last")
		end
		expect(callCounter.count >= atleast, `broad: effects ran {callCounter.count} < {atleast} times`)
	end
end

local function deepPropagation(bridge): () -> ()
	local len = 50
	local head = bridge.signal(0)
	local current = head
	for _ = 1, len do
		local c = current
		current = bridge.computed(function()
			return c.read() + 1
		end)
	end
	local callCounter = { count = 0 }
	bridge.effect(function()
		current.read()
		callCounter.count += 1
	end)
	local iter = 50
	return function()
		bridge.withBatch(function()
			head.write(1)
		end)
		local atleast = iter
		callCounter.count = 0
		for i = 0, iter - 1 do
			bridge.withBatch(function()
				head.write(i)
			end)
			expect(current.read() == len + i, "deep leaf")
		end
		expect(callCounter.count >= atleast, `deep: effect ran {callCounter.count} < {atleast} times`)
	end
end

local function diamond(bridge): () -> ()
	local width = 5
	local head = bridge.signal(0)
	local current = {}
	for _ = 1, width do
		table.insert(
			current,
			bridge.computed(function()
				return head.read() + 1
			end)
		)
	end
	local sum = bridge.computed(function()
		local s = 0
		for _, x in current do
			s += x.read()
		end
		return s
	end)
	local callCounter = { count = 0 }
	bridge.effect(function()
		sum.read()
		callCounter.count += 1
	end)
	return function()
		bridge.withBatch(function()
			head.write(1)
		end)
		expect(sum.read() == 2 * width, "diamond baseline")
		local atleast = 500
		callCounter.count = 0
		for i = 0, 499 do
			bridge.withBatch(function()
				head.write(i)
			end)
			expect(sum.read() == (i + 1) * width, "diamond sum")
		end
		expect(callCounter.count >= atleast, `diamond: effect ran {callCounter.count} < {atleast} times`)
	end
end

local function mux(bridge): () -> ()
	local heads = table.create(100)
	for k = 1, 100 do
		heads[k] = bridge.signal(0)
	end
	local muxed = bridge.computed(function()
		local t = table.create(100)
		for k, h in heads do
			t[k] = h.read()
		end
		return t
	end)
	-- as upstream: every index's first computed, then every index's second one
	local indexed = table.create(100)
	for k = 1, 100 do
		indexed[k] = bridge.computed(function()
			return muxed.read()[k]
		end)
	end
	local splited = table.create(100)
	for k, x in indexed do
		splited[k] = bridge.computed(function()
			return x.read() + 1
		end)
	end
	for _, x in splited do
		bridge.effect(function()
			x.read()
		end)
	end
	return function()
		for i = 0, 9 do
			bridge.withBatch(function()
				heads[i + 1].write(i)
			end)
			expect(splited[i + 1].read() == i + 1, "mux a")
		end
		for i = 0, 9 do
			bridge.withBatch(function()
				heads[i + 1].write(i * 2)
			end)
			expect(splited[i + 1].read() == i * 2 + 1, "mux b")
		end
	end
end

local function repeatedObservers(bridge): () -> ()
	local size = 30
	local head = bridge.signal(0)
	local current = bridge.computed(function()
		local result = 0
		for _ = 1, size do
			result += head.read()
		end
		return result
	end)
	local callCounter = { count = 0 }
	bridge.effect(function()
		current.read()
		callCounter.count += 1
	end)
	return function()
		bridge.withBatch(function()
			head.write(1)
		end)
		expect(current.read() == size, "repeated baseline")
		local atleast = 100
		callCounter.count = 0
		for i = 0, 99 do
			bridge.withBatch(function()
				head.write(i)
			end)
			expect(current.read() == i * size, "repeated")
		end
		expect(callCounter.count >= atleast, `repeated: effect ran {callCounter.count} < {atleast} times`)
	end
end

local function triangle(bridge): () -> ()
	local width = 10
	local head = bridge.signal(0)
	local current = head
	local list = {}
	for _ = 1, width do
		local c = current
		table.insert(list, current)
		current = bridge.computed(function()
			return c.read() + 1
		end)
	end
	local sum = bridge.computed(function()
		local s = 0
		for _, x in list do
			s += x.read()
		end
		return s
	end)
	local callCounter = { count = 0 }
	bridge.effect(function()
		sum.read()
		callCounter.count += 1
	end)
	return function()
		local constant = width * (width + 1) // 2
		bridge.withBatch(function()
			head.write(1)
		end)
		expect(sum.read() == constant, "triangle baseline")
		local atleast = 100
		callCounter.count = 0
		for i = 0, 99 do
			bridge.withBatch(function()
				head.write(i)
			end)
			expect(sum.read() == constant - width + i * width, "triangle sum")
		end
		expect(callCounter.count >= atleast, `triangle: effect ran {callCounter.count} < {atleast} times`)
	end
end

local function unstable(bridge): () -> ()
	local head = bridge.signal(0)
	local double = bridge.computed(function()
		return head.read() * 2
	end)
	local inverse = bridge.computed(function()
		return -head.read()
	end)
	local current = bridge.computed(function()
		local result = 0
		for _ = 1, 20 do
			result += if head.read() % 2 ~= 0 then double.read() else inverse.read()
		end
		return result
	end)
	local callCounter = { count = 0 }
	bridge.effect(function()
		current.read()
		callCounter.count += 1
	end)
	return function()
		bridge.withBatch(function()
			head.write(1)
		end)
		expect(current.read() == 40, "unstable baseline")
		local atleast = 100
		callCounter.count = 0
		for i = 0, 99 do
			bridge.withBatch(function()
				head.write(i)
			end)
			-- upstream reads nothing here (its per-iteration assert is commented out)
		end
		expect(callCounter.count >= atleast, `unstable: effect ran {callCounter.count} < {atleast} times`)
	end
end

-- mol (benches/kairo/molBench.ts, originally the $mol benchmark). Unlike the others this isn't
-- about graph shape: several nodes do real compute via `hard`, which adds fib(16) = 1597 of
-- throwaway work per call, so it measures recompute cost under load.
local function fib(n: number): number
	if n < 2 then
		return 1
	end
	return fib(n - 1) + fib(n - 2)
end

local function hard(n: number, _log: string): number
	return n + fib(16)
end

local numbers = { 0, 1, 2, 3, 4 }

local function mol(bridge): (boolean?) -> ()
	local res = {}
	local A = bridge.signal(0)
	local B = bridge.signal(0)
	local C = bridge.computed(function()
		return (A.read() % 2) + (B.read() % 2)
	end)
	local D = bridge.computed(function()
		local out = table.create(#numbers)
		for i, n in numbers do -- A and B are re-read per element, as upstream's map callback does
			out[i] = { x = n + (A.read() % 2) - (B.read() % 2) }
		end
		return out
	end)
	local E = bridge.computed(function()
		return hard(C.read() + A.read() + D.read()[1].x, "E")
	end)
	local F = bridge.computed(function()
		local d2 = D.read()[3].x -- upstream: D.read()[2].x || B.read()
		return hard(if d2 ~= 0 then d2 else B.read(), "F")
	end)
	local G = bridge.computed(function()
		-- upstream: C.read() + (C.read() || E.read() % 2) + D.read()[4].x + F.read()
		local c1 = C.read()
		local c2 = C.read()
		return c1 + (if c2 ~= 0 then c2 else E.read() % 2) + D.read()[5].x + F.read()
	end)
	bridge.effect(function() -- H
		table.insert(res, hard(G.read(), "H"))
	end)
	bridge.effect(function() -- I
		table.insert(res, G.read())
	end)
	bridge.effect(function() -- J
		table.insert(res, hard(F.read(), "J"))
	end)
	local i = 0
	return function(verify: boolean?)
		i += 1
		table.clear(res)
		bridge.withBatch(function()
			B.write(1)
			A.write(1 + i * 2)
		end)
		bridge.withBatch(function()
			A.write(2 + i * 2)
			B.write(2)
		end)
		if verify then
			expect(F.read() == 1599, "mol F")
			expect(G.read() == 1604, "mol G")
		end
	end
end

local cases = {
	{ name = "avoidable", fn = avoidablePropagation },
	{ name = "broad", fn = broadPropagation },
	{ name = "deep", fn = deepPropagation },
	{ name = "diamond", fn = diamond },
	-- heads[0] is written its own value (0) in both loops, so 18 of the 20 writes change a split
	{ name = "mux", fn = mux, effects = 18 },
	{ name = "repeated", fn = repeatedObservers },
	{ name = "triangle", fn = triangle },
	{ name = "unstable", fn = unstable },
	-- G changes in both batches (1604 -> 1607 -> 1604), so H and I each run twice; F never changes
	{ name = "mol", fn = mol, effects = 4 },
}

local ITERS = 50 -- iterations per timed run (upstream: 500); reported per iteration

-- builds the graph and runs upstream's three warm-up iterations
local function warm(bridge, fn)
	local iter = bridge.withBuild(function()
		return fn(bridge)
	end)
	iter()
	iter()
	iter()
	return iter
end

local M = { cases = {}, checks = {} }

for _, case in cases do
	local fn = case.fn
	table.insert(M.cases, {
		name = case.name,
		per = ITERS,
		run = function(bridge)
			warm(bridge, fn) -- upstream's warm-up round
			bridge.cleanup()
			harness.gc()
			local iter = warm(bridge, fn)
			local secs = harness.fastest(10, function()
				local t0 = os.clock()
				for _ = 1, ITERS do
					iter()
				end
				return os.clock() - t0
			end)
			iter(true)
			bridge.cleanup()
			return secs / ITERS
		end,
		evals = function(bridge)
			local computed, effect = harness.count(bridge, function(counted, reset)
				local iter = warm(counted, fn)
				reset()
				for _ = 1, ITERS do
					iter()
				end
			end)
			return (computed + effect) / ITERS
		end,
	})
	table.insert(M.checks, {
		name = case.name,
		run = function(bridge)
			local _, effect = harness.count(bridge, function(counted, reset)
				local iter = counted.withBuild(function()
					return fn(counted)
				end)
				iter(true)
				reset()
				iter(true) -- a second pass catches state carried between iterations
			end)
			if case.effects and effect < case.effects then
				harness.fail("wrong", `{case.name}: effects ran {effect} < {case.effects} times`)
			end
		end,
	})
end

return M
