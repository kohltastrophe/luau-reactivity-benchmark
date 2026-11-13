--!strict

-- Lifecycle benchmark measuring teardown cost, GC reclaim and tracing time, and memory retention
-- across 20k flat signal -> computed -> effect units.

local Types = require("../../adapters/types")
local harness = require("../harness")

type Signal = Types.Signal
type Framework = Types.Framework

export type MemoryStats = {
	N: number,
	liveKB: number,
	retainedKB: number,
	steadyKB: number,
	bytesPerNode: number,
}

local N = 20000 -- units, so 3N nodes: enough for teardown and collection to land in the millisecond range
local REPEATS = 10

local function buildCount(bridge: Framework, count: number)
	bridge.withBuild(function()
		for i = 1, count do
			local s = bridge.signal(i)
			local c = bridge.computed(function()
				return s.read() + 1
			end)
			bridge.effect(function()
				local _ = c.read()
			end)
		end
		return nil
	end)
end

local function build(bridge: Framework)
	buildCount(bridge, N)
end

local function collect(): number
	local t0 = os.clock()
	harness.gc()
	return os.clock() - t0
end

local function warm(bridge: Framework)
	build(bridge)
	bridge.cleanup()
	harness.gc()
end

-- Deterministic in a fresh process:
--   retainedKB   still held after the first build, cleanup(), and collect, so one-time caches land here
--   liveKB       steady-state built graph footprint above the post-warmup baseline
--   bytesPerNode liveKB over the 3N nodes (including bench closures)
--   steadyKB     mean incremental KB retained per further build/cleanup cycle; cumulative, so a leak below
--                gcinfo()'s 1 KB resolution still adds up
local function measureMemory(bridge: Framework, count: number, repeats: number): MemoryStats
	return harness.withCleanup(bridge, function()
		harness.gc()
		local base = gcinfo()
		buildCount(bridge, count)
		bridge.cleanup()
		harness.gc()
		local steadyBase = gcinfo()
		local retained = math.max(0, steadyBase - base)

		buildCount(bridge, count)
		harness.gc()
		local live = math.max(0, gcinfo() - steadyBase)
		bridge.cleanup()
		harness.gc()

		local loopBase = gcinfo()
		for _ = 1, repeats do
			buildCount(bridge, count)
			bridge.cleanup()
			harness.gc()
		end
		local steady = math.max(0, (gcinfo() - loopBase) / repeats)

		return {
			N = count,
			liveKB = live,
			retainedKB = retained,
			steadyKB = steady,
			bytesPerNode = live * 1024 / (3 * count),
		}
	end)
end

-- Each case warms up once and starts its timed phase right after a full collection, so one-time tables are
-- warm, the collector is idle, and only the graph's own cost is measured.
local cases: { Types.Case } = {
	{
		name = "teardown", -- cleanup() disposing the graph
		run = function(bridge: Framework): number
			return harness.withCleanup(bridge, function()
				warm(bridge)
				return harness.fastest(REPEATS, function()
					build(bridge)
					harness.gc()
					local t0 = os.clock()
					bridge.cleanup()
					return os.clock() - t0
				end)
			end)
		end,
	},
	{
		name = "collect", -- one full collection reclaiming the disposed graph
		run = function(bridge: Framework): number
			return harness.withCleanup(bridge, function()
				warm(bridge)
				return harness.fastest(REPEATS, function()
					build(bridge)
					harness.gc()
					bridge.cleanup()
					return collect()
				end)
			end)
		end,
	},
	{
		name = "live", -- one full collection tracing the live graph, with nothing to reclaim
		run = function(bridge: Framework): number
			return harness.withCleanup(bridge, function()
				warm(bridge)
				return harness.fastest(REPEATS, function()
					build(bridge)
					harness.gc()
					local secs = collect()
					bridge.cleanup()
					return secs
				end)
			end)
		end,
	},
}

local checks: { Types.Check } = {
	{
		name = "teardown",
		run = function(bridge: Framework)
			harness.withCleanup(bridge, function()
				local n = 100
				local sources = table.create(n) :: { Signal }
				local sum, after = 0, 0
				local cleaned = false
				bridge.withBuild(function()
					for i = 1, n do
						local s = bridge.signal(i)
						sources[i] = s
						local c = bridge.computed(function()
							return s.read() + 1
						end)
						bridge.effect(function()
							local v = c.read()
							if cleaned then
								after += 1
							else
								sum += v
							end
						end)
					end
					return nil
				end)
				local want = n * (n + 3) // 2
				harness.expect(sum == want, `teardown: initial sum {sum} ~= {want}`)
				cleaned = true
				bridge.cleanup()
				bridge.withBatch(function()
					for i = 1, n do
						sources[i].write(i + 10)
					end
				end)
				harness.expect(after == 0, `teardown: {after} disposed effect(s) ran after cleanup`)
				return nil
			end)
		end,
	},
	{
		name = "memory",
		run = function(bridge: Framework)
			local stats = measureMemory(bridge, 200, 2)
			harness.expect(stats.N == 200, "memory N")
			harness.expect(stats.liveKB >= 0 and stats.liveKB < math.huge, "memory liveKB")
			harness.expect(stats.retainedKB >= 0 and stats.retainedKB < math.huge, "memory retainedKB")
			harness.expect(stats.steadyKB >= 0 and stats.steadyKB < math.huge, "memory steadyKB")
			harness.expect(stats.bytesPerNode >= 0 and stats.bytesPerNode < math.huge, "memory bytesPerNode")
		end,
	},
}

local M = {
	cases = cases,
	checks = checks,
	memory = function(bridge: Framework): MemoryStats
		return measureMemory(bridge, N, REPEATS)
	end,
}

return M
