-- Lifecycle bench: teardown/dispose cost, GC reclaim time, and memory footprint/retention.
--
-- Not one of upstream's four suites. It realises the intent upstream states but never finished
-- ("Tracks garbage collection overhead per test ... We're also working on enabling consistent
-- logging and efficient tracking of GC time"): V8 makes per-test GC accounting awkward, so the JS
-- benchmark only force-collects *between* tests to keep GC out of the timings, never measuring it.
--
-- Luau's collectgarbage exposes exactly two operations -- "collect" (a full stop-the-world cycle)
-- and "count" (live bytes, exact) -- and nothing incremental (no step/stop/pause). That rules out
-- "GC time during a propagation run" (transient churn can't be isolated without an allocation
-- hook) but makes the *lifecycle* of a graph precisely measurable: build it, dispose it via the
-- adapter's cleanup(), then force one full collection of the corpse. Each is timed on its own; the
-- split matters because frameworks divide the cost differently -- an owner-tree teardown unlinks
-- eagerly (pays at cleanup), while a teardown that only drops effect handles defers reclamation to
-- the collector (pays at collect). "count" additionally gives a deterministic memory footprint and
-- a retained-after-cleanup figure (a leak / over-retention indicator) that V8 can't report cleanly.

local clock = os.clock

local M = {}

-- N independent units; 3N reactive nodes total (signal + computed + effect). Sized so teardown and
-- collection land in the millisecond range (well clear of the ~125ns clock and run-to-run jitter),
-- and so the framework's own memory dominates the live heap, making "collect" framework-sensitive.
local N = 20000
M.N = N

local REPEATS = 10

-- A flat forest of (signal -> computed -> effect) units. Deliberately depth-1: build is strictly
-- O(N) for *every* framework (eager engines evaluate each effect in O(1)), so unlike a deep chain
-- or a lattice the construction cost can't pathologically diverge -- what differs is the per-node
-- memory each framework allocates and what its cleanup() / the collector then have to undo.
function M.build(bridge, n: number)
	bridge.withBuild(function()
		for i = 1, n do
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

local function full_gc()
	collectgarbage("collect")
	collectgarbage("collect")
end

-- teardown: wall-clock of cleanup() disposing one freshly built graph. Rebuilt every repeat (since
-- cleanup destroys it); a full GC right before the clock starts leaves the collector in a fresh
-- state so it is unlikely to fire mid-teardown and pollute the reading. fastest-of-N.
function M.teardown(bridge): number
	local best
	for _ = 1, REPEATS do
		M.build(bridge, N)
		full_gc()
		local t0 = clock()
		bridge.cleanup()
		local dt = clock() - t0
		full_gc() -- reclaim the corpse before the next build (untimed)
		if not best or dt < best then
			best = dt
		end
	end
	return best :: number
end

-- collect: wall-clock of one full collection reclaiming a built-then-disposed graph -- the cost a
-- lazy teardown defers to the collector. The leading full_gc both gives a clean slate and frees the
-- previous iteration's corpse, so the timed collect reclaims only this iteration's graph. fastest-of-N.
function M.collect(bridge): number
	local best
	for _ = 1, REPEATS do
		full_gc()
		M.build(bridge, N)
		bridge.cleanup() -- dispose: the graph is now unreachable garbage
		local t0 = clock()
		collectgarbage("collect") -- time reclaiming it
		local dt = clock() - t0
		if not best or dt < best then
			best = dt
		end
	end
	return best :: number
end

-- memory: deterministic in a fresh process. Reports the graph footprint plus two retention figures that answer different questions:
--   liveKB     = footprint of the built graph (KB above a collected baseline)
--   retainedKB = build-size retention: KB still held after the *first* build+cleanup()+GC in a fresh process
--   steadyKB   = steady-state retention: best-of-N of the *incremental* KB retained by subsequent build+cleanup cycles.
--                It isolates whether repeatedly building and disposing graphs grows the heap without bound:
--                a genuine per-build leak stays positive every cycle, a framework that fully releases each graph reads ~0.
--   bytesPerNode = liveKB spread over the 3N reactive nodes. NB this is "bytes per node as used through the bench bridge":
--                  liveKB also includes the per-unit adapter wrappers ({read,write} / {read} tables + their closures) and the
--                  user computed/effect closures, which are identical for every framework.
function M.stats(bridge): { N: number, liveKB: number, retainedKB: number, steadyKB: number, bytesPerNode: number }
	-- build-size retention: must be the first build in the process so one-time caches land here.
	full_gc()
	local base = collectgarbage("count")
	M.build(bridge, N)
	full_gc()
	local live = collectgarbage("count") - base
	bridge.cleanup()
	full_gc()
	local retained = collectgarbage("count") - base

	-- steady-state retention: with the one-time costs now resident, measure the incremental
	-- retain of each further cycle against a baseline taken just before that cycle's build.
	local steady = math.huge
	for _ = 1, REPEATS do
		full_gc()
		local cycleBase = collectgarbage("count")
		M.build(bridge, N)
		full_gc()
		bridge.cleanup()
		full_gc()
		local r = collectgarbage("count") - cycleBase
		if r < steady then
			steady = r
		end
	end
	steady = math.max(0, steady)

	return { N = N, liveKB = live, retainedKB = retained, steadyKB = steady, bytesPerNode = live * 1024 / (3 * N) }
end

-- Each case returns its own fastest-of-N seconds (suite.luau just pcalls it, unlike the other
-- suites whose cases are wrapped in the shared fastest() helper).
M.cases = {
	{ name = "teardown", fn = M.teardown },
	{ name = "collect", fn = M.collect },
}

return M
