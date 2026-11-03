--!strict

-- Port of the kairo suite from milomg/js-reactivity-benchmark (packages/core/src/benches/kairo/*).
--
-- Each case builds upstream's graph and checks its value assertions. Upstream's effect counts are
-- checked as a floor so non-glitch-free libraries pass while adapters whose effects never re-run fail.

local Types = require("../../adapters/types")
local harness = require("../harness")

type Computed = Types.Computed
type Signal = Types.Signal
type Framework = Types.Framework

local expect = harness.expect
local expectFloor = harness.expectFloor

local sink = 0
local function busy(): number
	local a = 0
	for _ = 1, 100 do
		a += 1
	end
	sink = bit32.bxor(sink, a)
	return a
end

local function avoidablePropagation(bridge: Framework): (boolean?) -> ()
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

local function broadPropagation(bridge: Framework): (boolean?) -> ()
	local head = bridge.signal(0)
	local last: Computed = (head :: unknown) :: Computed
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
		expectFloor("broad", callCounter.count, atleast)
	end
end

local function deepPropagation(bridge: Framework): (boolean?) -> ()
	local len = 50
	local head = bridge.signal(0)
	local current: Computed = (head :: unknown) :: Computed
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
		expectFloor("deep", callCounter.count, atleast)
	end
end

local function diamond(bridge: Framework): (boolean?) -> ()
	local width = 5
	local head = bridge.signal(0)
	local current: { Computed } = {}
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
		expectFloor("diamond", callCounter.count, atleast)
	end
end

local function mux(bridge: Framework): (boolean?) -> ()
	local heads = table.create(100) :: { Signal }
	for k = 1, 100 do
		heads[k] = bridge.signal(0)
	end
	local muxed = bridge.computed(function()
		local t = table.create(100) :: { number }
		for k, h in heads do
			t[k] = h.read()
		end
		return t
	end)
	-- as upstream: every index's first computed, then every index's second one
	local indexed = table.create(100) :: { Computed }
	for k = 1, 100 do
		indexed[k] = bridge.computed(function()
			return muxed.read()[k]
		end)
	end
	local splited = table.create(100) :: { Computed }
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

local function repeatedObservers(bridge: Framework): (boolean?) -> ()
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
		expectFloor("repeated", callCounter.count, atleast)
	end
end

local function triangle(bridge: Framework): (boolean?) -> ()
	local width = 10
	local head = bridge.signal(0)
	local current: Computed = (head :: unknown) :: Computed
	local list: { Computed } = {}
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
		expectFloor("triangle", callCounter.count, atleast)
	end
end

local function unstable(bridge: Framework): (boolean?) -> ()
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
	return function(verify: boolean?)
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
		expectFloor("unstable", callCounter.count, atleast)
		if verify then
			expect(current.read() == 3960, "unstable final")
		end
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

local function mol(bridge: Framework): (boolean?) -> ()
	local res: { number } = {}
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

type KairoDef = {
	name: string,
	fn: (Framework) -> (boolean?) -> (),
	effects: number?,
}

local cases: { KairoDef } = {
	{ name = "avoidable", fn = avoidablePropagation },
	{ name = "broad", fn = broadPropagation },
	{ name = "deep", fn = deepPropagation },
	{ name = "diamond", fn = diamond },
	-- heads[1] is written its own value (0) in both loops, so 18 of the 20 writes change a split
	{ name = "mux", fn = mux, effects = 18 },
	{ name = "repeated", fn = repeatedObservers },
	{ name = "triangle", fn = triangle },
	{ name = "unstable", fn = unstable },
	-- G changes in both batches (1604 -> 1607 -> 1604), so H and I each run twice; F never changes
	{ name = "mol", fn = mol, effects = 4 },
}

local ITERS = 50 -- iterations per timed run (upstream: 500); reported per iteration

-- Builds the graph and runs upstream's three warm-up iterations.
local function warm(bridge: Framework, fn: (Framework) -> (boolean?) -> (), enforceDeadline: boolean): (boolean?) -> ()
	local t0 = os.clock()
	local iter = bridge.withBuild(function()
		return fn(bridge)
	end)
	iter()
	iter()
	iter()
	if enforceDeadline then
		harness.checkDeadline(os.clock() - t0, "warmup")
	end
	return iter
end

local M: Types.Suite = { cases = {}, checks = {} }

for _, case in cases do
	local fn = case.fn
	table.insert(M.cases, {
		name = case.name,
		per = ITERS,
		run = function(bridge: Framework): number
			return harness.withCleanup(bridge, function()
				warm(bridge, fn, true) -- upstream's warm-up round
				bridge.cleanup()
				harness.gc()
				local iter = warm(bridge, fn, true)
				local secs = harness.fastest(10, function()
					local t0 = os.clock()
					for _ = 1, ITERS do
						iter()
					end
					return os.clock() - t0
				end)
				iter(true)
				return secs / ITERS
			end)
		end,
		evals = function(bridge: Framework): number
			local computed, effect = harness.count(bridge, function(counted, reset)
				local iter = warm(counted, fn, false)
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
		run = function(bridge: Framework)
			local initialEffects = 0
			local _, effect = harness.count(bridge, function(counted, reset)
				local iter = counted.withBuild(function()
					return fn(counted)
				end)
				iter(true)
				local _, initE = reset()
				initialEffects = initE
				iter(true) -- a second pass catches state carried between iterations
			end)
			expectFloor(`{case.name} (build)`, initialEffects, 1)
			if case.effects then
				expectFloor(case.name, effect, case.effects)
			end
		end,
	})
end

return M
