-- Lifecycle bench (not one of upstream's suites): teardown cost, GC reclaim and tracing time, and memory
-- footprint/retention, over a flat forest of signal -> computed -> effect units. Depth 1 keeps the build
-- O(N) on every framework, so what differs is the memory each one allocates and what cleanup() and the
-- collector then have to undo. See the README's "Teardown, GC, and memory".

local harness = require("../harness")

local N = 20000 -- units, so 3N nodes: enough for teardown and collection to land in the millisecond range
local REPEATS = 10

local function build(bridge)
	bridge.withBuild(function()
		for i = 1, N do
			local s = bridge.signal(i)
			local c = bridge.computed(function()
				return s.read() + 1
			end)
			bridge.effect(function()
				local _ = c.read()
			end)
		end
	end)
end

local function collect(): number
	local t0 = os.clock()
	harness.gc()
	return os.clock() - t0
end

local M = {}

-- Each starts its timed phase right after a full collection, so the collector is idle and only the
-- graph's own cost is measured.
M.cases = {
	{
		name = "teardown", -- cleanup() disposing the graph
		run = function(bridge)
			return harness.fastest(REPEATS, function()
				build(bridge)
				harness.gc()
				local t0 = os.clock()
				bridge.cleanup()
				return os.clock() - t0
			end)
		end,
	},
	{
		name = "collect", -- one full collection reclaiming the disposed graph
		run = function(bridge)
			return harness.fastest(REPEATS, function()
				build(bridge)
				harness.gc()
				bridge.cleanup()
				return collect()
			end)
		end,
	},
	{
		name = "live", -- one full collection tracing the live graph, with nothing to reclaim
		run = function(bridge)
			return harness.fastest(REPEATS, function()
				build(bridge)
				harness.gc()
				local secs = collect()
				bridge.cleanup()
				return secs
			end)
		end,
	},
}

M.checks = {}

-- Deterministic in a fresh process:
--   liveKB       the built graph's footprint above a collected baseline
--   bytesPerNode liveKB over the 3N nodes (including the bench closures and adapter wrappers)
--   retainedKB   still held after the first build, cleanup() and collect, so one-time caches land here
--   steadyKB     mean incremental KB retained per further build/cleanup cycle; cumulative, so a leak below
--                count's 1 KB resolution still adds up
function M.memory(bridge)
	harness.gc()
	local base = collectgarbage("count")
	build(bridge)
	harness.gc()
	local live = collectgarbage("count") - base
	bridge.cleanup()
	harness.gc()
	local retained = collectgarbage("count") - base

	local steadyBase = collectgarbage("count")
	for _ = 1, REPEATS do
		build(bridge)
		bridge.cleanup()
		harness.gc()
	end
	local steady = math.max(0, (collectgarbage("count") - steadyBase) / REPEATS)

	return { N = N, liveKB = live, retainedKB = retained, steadyKB = steady, bytesPerNode = live * 1024 / (3 * N) }
end

return M
