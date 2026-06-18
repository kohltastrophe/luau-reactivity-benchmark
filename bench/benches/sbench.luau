-- Port of benches/sBench.ts from milomg/js-reactivity-benchmark (itself the SolidJS bench).

-- Counts are scaled down for headless luau (upstream uses 1e5) but the sub-pattern set and each
-- pattern's n/scount ratios are upstream's. Create and update use separate bases (COUNT vs UCOUNT):
-- creation is O(count) so a big COUNT gives a solid throughput signal, while an update's cost is
-- count * observers-per-write, so the 1000-observer pattern needs a smaller UCOUNT to stay under the
-- cap on eager frameworks. Teardown is excluded from these timings (the gc suite measures it on its
-- own); upstream happens to leave cleanup inside its timed window, which conflates the two.

local harness = require("../harness")
local clock = os.clock

local M = {}

local COUNT = 20000 -- create patterns: O(COUNT), so a high count gives a solid throughput signal
local UCOUNT = 1500 -- update patterns scale to COUNT*observers per write, so an eager framework
-- with the 1000-observer pattern does ~1000*writes reruns; a smaller base keeps that under the cap
-- without changing the sub-pattern ratios (the chart normalises per row, so absolute scale is moot)
local REPEATS = 5

local function run(bridge, fn, n: number, scount: number): number
	local best
	for _ = 1, REPEATS do
		harness.gc()
		local secs
		bridge.withBuild(function()
			local sources = table.create(scount)
			for i = 1, scount do
				sources[i] = bridge.signal(i)
			end
			for i = 1, scount do
				sources[i].read()
				sources[i].read()
				sources[i].read()
			end
			local t0 = clock()
			local update = fn(bridge, n, sources)
			if update then
				update()
			end
			secs = clock() - t0
		end)
		bridge.cleanup()
		if not best or secs < best then
			best = secs
		end
	end
	return best :: number
end

-- ---- creation sub-patterns (each builds effects over `sources`, returns nothing) --------------

local function createSignals(bridge, n: number, _sources)
	local out = table.create(n)
	for i = 1, n do
		out[i] = bridge.signal(i)
	end
	return nil
end

local function create0to1(bridge, n: number, _sources) -- effects that read nothing
	for i = 1, n do
		bridge.effect(function()
			return i
		end)
	end
	return nil
end

local function create1to1(bridge, n: number, sources)
	for i = 1, n do
		local get = sources[i].read
		bridge.effect(function()
			return get()
		end)
	end
	return nil
end

local function create2to1(bridge, n: number, sources)
	for i = 1, n do
		local a, b = sources[2 * i - 1].read, sources[2 * i].read
		bridge.effect(function()
			return a() + b()
		end)
	end
	return nil
end

local function create4to1(bridge, n: number, sources)
	for i = 1, n do
		local a, b = sources[4 * i - 3].read, sources[4 * i - 2].read
		local c, d = sources[4 * i - 1].read, sources[4 * i].read
		bridge.effect(function()
			return a() + b() + c() + d()
		end)
	end
	return nil
end

local function create1000to1(bridge, n: number, sources) -- each effect reads 1000 sources
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
	return nil
end

-- fan-out: one source read by `fan` effects (loops n / fan times)
local function fanOut(fan: number)
	return function(bridge, n: number, sources)
		for i = 1, n // fan do
			local get = sources[i].read
			for _ = 1, fan do
				bridge.effect(function()
					return get()
				end)
			end
		end
		return nil
	end
end

-- ---- update sub-patterns (each wires effect(s), returns an updater over signal 1) -------------

local function updateFanIn(fan: number) -- effect reads `fan` signals; we drive the first
	return function(bridge, n: number, sources)
		local set = sources[1].write
		bridge.effect(function()
			local sum = 0
			for i = 1, fan do
				sum += sources[i].read()
			end
			return sum
		end)
		return function()
			for i = 1, n do
				bridge.withBatch(function()
					set(i)
				end)
			end
		end
	end
end

local function updateFanOut(observers: number) -- one signal observed by `observers` effects
	return function(bridge, n: number, sources)
		local get, set = sources[1].read, sources[1].write
		for _ = 1, observers do
			bridge.effect(function()
				return get()
			end)
		end
		return function()
			for i = 1, n do
				bridge.withBatch(function()
					set(i)
				end)
			end
		end
	end
end

-- ---- reported rows ---------------------------------------------------------------------------

function M.createSignals(bridge): number
	return run(bridge, createSignals, COUNT, COUNT)
end

function M.createComputations(bridge): number
	local total = 0
	total += run(bridge, create0to1, COUNT, 0)
	total += run(bridge, create1to1, COUNT, COUNT)
	total += run(bridge, create2to1, COUNT // 2, COUNT)
	total += run(bridge, create4to1, COUNT // 4, COUNT)
	total += run(bridge, create1000to1, COUNT // 1000, COUNT)
	total += run(bridge, fanOut(2), COUNT, COUNT // 2)
	total += run(bridge, fanOut(4), COUNT, COUNT // 4)
	total += run(bridge, fanOut(8), COUNT, COUNT // 8)
	total += run(bridge, fanOut(1000), COUNT, COUNT // 1000)
	return total
end

function M.updateSignals(bridge): number
	local total = 0
	total += run(bridge, updateFanIn(1), UCOUNT * 4, 1)
	total += run(bridge, updateFanIn(2), UCOUNT * 2, 2)
	total += run(bridge, updateFanIn(4), UCOUNT, 4)
	total += run(bridge, updateFanIn(1000), UCOUNT // 250, 1000)
	total += run(bridge, updateFanOut(2), UCOUNT, 1)
	total += run(bridge, updateFanOut(4), UCOUNT, 1)
	total += run(bridge, updateFanOut(1000), UCOUNT // 10, 1) -- upstream runs n/10 here (1000 observers)
	return total
end

M.cases = {
	{ name = "createSignals", fn = M.createSignals },
	{ name = "createComputations", fn = M.createComputations },
	{ name = "updateSignals", fn = M.updateSignals },
}

return M
