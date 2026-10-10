-- Port of benches/sBench.ts from milomg/js-reactivity-benchmark (itself the SolidJS bench).
--
-- Sizes are scaled down for headless luau (upstream uses 1e5 for both) but every pattern keeps upstream's
-- n/scount ratios. Updates use a smaller base because their cost is count * observers-per-write.
--
-- The timing is upstream's `run`: three untimed warm-up builds at n/100, then fastest-of-10 runs, each
-- building and reading (three times) its sources untimed inside withBuild, starting the clock before the
-- pattern wires its effects, and stopping it after the update phase and cleanup().

local harness = require("../harness")

local COUNT = 20000 -- create patterns
local UCOUNT = 1500 -- update patterns

local function signals(bridge, scount: number)
	local sources = table.create(scount)
	for i = 1, scount do
		sources[i] = bridge.signal(i - 1)
	end
	return sources
end

-- One build (`build` returns the pattern's update phase, if any), its update, then cleanup().
local function buildAndUpdate(bridge, build: () -> (() -> ())?)
	local update = bridge.withBuild(build)
	if update then
		update()
	end
	bridge.cleanup()
end

-- The fastest run of one pattern, and the timed seconds its repeats spent.
local function run(bridge, p, budget: number?): (number, number)
	for _ = 1, 3 do -- a JS loop to n/100 runs ceil(n/100) times
		buildAndUpdate(bridge, function()
			return p.fn(bridge, math.ceil(p.n / 100), signals(bridge, p.scount))
		end)
	end
	return harness.runs(10, function()
		local t0
		buildAndUpdate(bridge, function()
			local sources = signals(bridge, p.scount)
			for i = 1, p.scount do
				local read = sources[i].read
				read()
				read()
				read()
			end
			t0 = os.clock()
			return p.fn(bridge, p.n, sources)
		end)
		return os.clock() - t0
	end, budget)
end

-- A row is upstream's sum of its patterns' fastest times. The patterns share one repeat budget (each gets an
-- even share of what's left, so fast ones hand their unused time on), and the row is judged against the
-- deadline as a whole, like any other bench's single run.
local function row(bridge, patterns): number
	local total, left = 0, harness.BUDGET
	for i, p in patterns do
		local best, spent = run(bridge, p, math.max(0, left) / (#patterns - i + 1))
		total += best
		left -= spent
	end
	harness.checkDeadline(total, "row")
	return total
end

-- ---- creation patterns: effects over `sources` ------------------------------------------------

local function createSignals(bridge, n: number, sources) -- overwrites the prebuilt sources, as upstream
	for i = 1, n do
		sources[i] = bridge.signal(i - 1)
	end
end

local function create0to1(bridge, n: number, _sources)
	for i = 1, n do
		bridge.effect(function()
			return i
		end)
	end
end

local function create1to1(bridge, n: number, sources)
	for i = 1, n do
		local get = sources[i].read
		bridge.effect(function()
			return get()
		end)
	end
end

local function create2to1(bridge, n: number, sources)
	for i = 1, n do
		local a, b = sources[2 * i - 1].read, sources[2 * i].read
		bridge.effect(function()
			return a() + b()
		end)
	end
end

local function create4to1(bridge, n: number, sources)
	for i = 1, n do
		local a, b = sources[4 * i - 3].read, sources[4 * i - 2].read
		local c, d = sources[4 * i - 1].read, sources[4 * i].read
		bridge.effect(function()
			return a() + b() + c() + d()
		end)
	end
end

local function create1000to1(bridge, n: number, sources)
	for i = 1, n do
		local offset = (i - 1) * 1000
		bridge.effect(function()
			local sum = 0
			for j = 1, 1000 do
				sum += sources[offset + j].read()
			end
			return sum
		end)
	end
end

local function createFanOut(fan: number) -- one source read by `fan` effects
	return function(bridge, n: number, sources)
		for i = 1, math.ceil(n / fan) do
			local get = sources[i].read
			for _ = 1, fan do
				bridge.effect(function()
					return get()
				end)
			end
		end
	end
end

-- ---- update patterns: effect(s) over signal 1, then n batched writes to it ---------------------

local function updater(bridge, set: (number) -> (), n: number): () -> ()
	return function()
		for i = 1, n do
			bridge.withBatch(function()
				set(i - 1)
			end)
		end
	end
end

local function update1to1(bridge, n: number, sources)
	local get1, set1 = sources[1].read, sources[1].write
	bridge.effect(function()
		return get1()
	end)
	return updater(bridge, set1, n)
end

local function update2to1(bridge, n: number, sources)
	local get1, set1 = sources[1].read, sources[1].write
	local get2 = sources[2].read
	bridge.effect(function()
		return get1() + get2()
	end)
	return updater(bridge, set1, n)
end

local function update4to1(bridge, n: number, sources)
	local get1, set1 = sources[1].read, sources[1].write
	local get2, get3, get4 = sources[2].read, sources[3].read, sources[4].read
	bridge.effect(function()
		return get1() + get2() + get3() + get4()
	end)
	return updater(bridge, set1, n)
end

local function update1000to1(bridge, n: number, sources)
	local set1 = sources[1].write
	bridge.effect(function()
		local sum = 0
		for i = 1, 1000 do
			sum += sources[i].read()
		end
		return sum
	end)
	return updater(bridge, set1, n)
end

local function updateFanOut(observers: number) -- one signal observed by `observers` effects
	return function(bridge, n: number, sources)
		local get1, set1 = sources[1].read, sources[1].write
		for _ = 1, observers do
			bridge.effect(function()
				return get1()
			end)
		end
		return updater(bridge, set1, n)
	end
end

-- ---- patterns at a base size; `observers` is how many effects each write reaches ---------------

local function creates(count: number)
	return {
		{ name = "create0to1", fn = create0to1, n = count, scount = 0 },
		{ name = "create1to1", fn = create1to1, n = count, scount = count },
		{ name = "create2to1", fn = create2to1, n = count // 2, scount = count },
		{ name = "create4to1", fn = create4to1, n = count // 4, scount = count },
		{ name = "create1000to1", fn = create1000to1, n = count // 1000, scount = count },
		{ name = "create1to2", fn = createFanOut(2), n = count, scount = count // 2 },
		{ name = "create1to4", fn = createFanOut(4), n = count, scount = count // 4 },
		{ name = "create1to8", fn = createFanOut(8), n = count, scount = count // 8 },
		{ name = "create1to1000", fn = createFanOut(1000), n = count, scount = count // 1000 },
	}
end

local function updates(count: number)
	return {
		{ name = "update1to1", fn = update1to1, n = count * 4, scount = 1, observers = 1 },
		{ name = "update2to1", fn = update2to1, n = count * 2, scount = 2, observers = 1 },
		{ name = "update4to1", fn = update4to1, n = count, scount = 4, observers = 1 },
		{ name = "update1000to1", fn = update1000to1, n = count // 250, scount = 1000, observers = 1 },
		{ name = "update1to2", fn = updateFanOut(2), n = count, scount = 1, observers = 2 },
		{ name = "update1to4", fn = updateFanOut(4), n = count, scount = 1, observers = 4 },
		{ name = "update1to1000", fn = updateFanOut(1000), n = count // 10, scount = 1, observers = 1000 }, -- upstream: n/10
	}
end

local M = {
	cases = {
		{
			name = "createSignals",
			run = function(bridge)
				return (run(bridge, { fn = createSignals, n = COUNT, scount = COUNT }))
			end,
		},
		{
			name = "createComputations",
			run = function(bridge)
				return row(bridge, creates(COUNT))
			end,
		},
		{
			name = "updateSignals",
			run = function(bridge)
				return row(bridge, updates(UCOUNT))
			end,
		},
	},
	checks = {},
}

-- Upstream checks nothing here. Every pattern runs once at a small size, and must make at least the effect
-- runs a framework that skips equal writes would: one per effect created, then one per observer for each
-- write but the first (which writes signal 1 its current value, 0), so n * observers for an update.
local checked = creates(2000)
table.move(updates(1000), 1, 7, #checked + 1, checked)
for _, p in checked do
	local floor = p.n * (p.observers or 1)
	table.insert(M.checks, {
		name = p.name,
		run = function(bridge)
			local _, effect = harness.count(bridge, function(counted)
				buildAndUpdate(counted, function()
					return p.fn(counted, p.n, signals(counted, p.scount))
				end)
			end)
			if effect < floor then
				harness.fail("wrong", `{p.name}: effects ran {effect} < {floor} times`)
			end
		end,
	})
end

return M
