--!strict

-- Port of the reactively "dynamic graph" benchmark from milomg/js-reactivity-benchmark
-- (benches/reactively/dependencyGraph.ts + config.ts). Uses upstream's seeded RNG with scaled-down
-- iteration counts and golden leaf sums from bench/golden.ts.

local Types = require("../../adapters/types")
local harness = require("../harness")

type Computed = Types.Computed
type Signal = Types.Signal
type Framework = Types.Framework

type Config = {
	name: string?,
	width: number,
	totalLayers: number,
	staticFraction: number,
	nSources: number,
	readFraction: number,
	iterations: number,
	expected: number,
	floor: number?,
}

type Graph = {
	sources: { Signal },
	readLeaves: { Computed },
	counter: { count: number },
}

local function makeRow(
	sources: { Computed },
	counter: { count: number },
	staticFraction: number,
	nSources: number,
	bridge: Framework,
	random: () -> number
): { Computed }
	local row = table.create(#sources) :: { Computed }
	for myDex = 1, #sources do
		local mySources = table.create(nSources) :: { Computed }
		for sourceDex = 0, nSources - 1 do
			mySources[sourceDex + 1] = sources[((myDex - 1 + sourceDex) % #sources) + 1]
		end

		if random() < staticFraction then
			row[myDex] = bridge.computed(function()
				counter.count += 1
				local sum = 0
				for _, src in mySources do
					sum += src.read()
				end
				return sum
			end)
		else
			-- dynamic node: drops one of its sources depending on the value of the first
			local first = mySources[1]
			local tail: { Computed } = table.move(mySources, 2, #mySources, 1, {})
			row[myDex] = bridge.computed(function()
				counter.count += 1
				local sum = first.read()
				local shouldDrop = sum % 2 == 1
				local dropDex = math.fmod(sum, #tail) -- exact, like JS %, where Luau's % rounds above 2^53
				for i = 1, #tail do
					if shouldDrop and (i - 1) == dropDex then
						continue
					end
					sum += tail[i].read()
				end
				return sum
			end)
		end
	end
	return row
end

local function removeElems(src: { Computed }, rmCount: number, rand: () -> number): { Computed }
	local copy = table.clone(src)
	for _ = 1, rmCount do
		table.remove(copy, math.floor(rand() * #copy) + 1)
	end
	return copy
end

-- Returns { sources, readLeaves }. Every computed bumps a per-graph counter, as upstream's do.
local function makeGraph(bridge: Framework, config: Config): Graph
	return bridge.withBuild(function()
		local counter = { count = 0 }
		local sources = table.create(config.width) :: { Signal }
		local prevRow = table.create(config.width) :: { Computed }
		for i = 1, config.width do
			local s = bridge.signal(i - 1)
			sources[i] = s
			prevRow[i] = (s :: unknown) :: Computed
		end

		local nodeRand = harness.rng()
		for _ = 1, config.totalLayers - 1 do
			prevRow = makeRow(prevRow, counter, config.staticFraction, config.nSources, bridge, nodeRand)
		end

		local leafRand = harness.rng()
		local skipCount = math.round(#prevRow * (1 - config.readFraction))
		local readLeaves = removeElems(prevRow, skipCount, leafRand)

		bridge.effect(function()
			for _, leaf in readLeaves do
				leaf.read()
			end
		end)

		return { sources = sources, readLeaves = readLeaves, counter = counter }
	end)
end

-- Writes one source per iteration and reads the chosen leaves; returns the final leaf sum. Timed runs and
-- checks pass enforceDeadline = true so runaway propagations abort cooperatively at DEADLINE.
local function runGraph(graph: Graph, iterations: number, bridge: Framework, enforceDeadline: boolean): number
	local sources, readLeaves = graph.sources, graph.readLeaves
	local n = #sources
	local deadline = if enforceDeadline then os.clock() + harness.DEADLINE else nil
	for i = 0, iterations - 1 do
		local sourceDex = i % n
		bridge.withBatch(function()
			sources[sourceDex + 1].write(i + sourceDex)
		end)
		for _, leaf in readLeaves do
			leaf.read()
		end
		if deadline and os.clock() > deadline then
			harness.fail("timeout", "dynamic deadline exceeded")
		end
	end
	local sum = 0
	for _, leaf in readLeaves do
		sum += leaf.read()
	end
	return sum
end

local function expectSum(sum: number, config: Config)
	if sum ~= config.expected then
		harness.fail("wrong", string.format("wrong value: sum = %.17g, expected %.17g", sum, config.expected))
	end
end

local function percent(n: number): string
	return math.round(n * 100) .. "%"
end

local function title(config: Config): string
	local dyn = if config.staticFraction < 1 then " - dyn" .. percent(1 - config.staticFraction) else ""
	local read = if config.readFraction < 1 then " - lazy" .. percent(1 - config.readFraction) else ""
	return string.format("%d-%dx%d%s%s", config.nSources, config.width, config.totalLayers, dyn, read)
end

-- The timed configs: upstream's config.ts shapes, iterations scaled down for headless luau.
-- Keep one `{ ... }` literal per line for bench/golden.ts.
-- stylua: ignore
local configs: { Config } = {
	{ width = 10, totalLayers = 5, staticFraction = 1, nSources = 2, readFraction = 0.2, iterations = 12000, expected = 383968 },
	{ width = 10, totalLayers = 10, staticFraction = 3 / 4, nSources = 6, readFraction = 0.2, iterations = 2000, expected = 40290686860 },
	{ width = 1000, totalLayers = 12, staticFraction = 0.95, nSources = 4, readFraction = 1, iterations = 2000, expected = 8384413696000 },
	{ width = 1000, totalLayers = 5, staticFraction = 1, nSources = 25, readFraction = 1, iterations = 2000, expected = 780859375000 },
	{ width = 5, totalLayers = 500, staticFraction = 1, nSources = 3, readFraction = 1, iterations = 300, expected = 1.8119545411608538e241 },
	{ width = 100, totalLayers = 15, staticFraction = 0.5, nSources = 6, readFraction = 1, iterations = 1000, expected = 7828579993190400 },
}

-- Upstream's unit tests (frameworks.test.ts): tiny graphs with known sums, verification only.
-- Keep one `{ ... }` literal per line for bench/golden.ts.
-- stylua: ignore
local tests: { Config } = {
	{ name = "static 3x3", width = 3, totalLayers = 3, staticFraction = 1, nSources = 2, readFraction = 1, iterations = 2, expected = 16, floor = 11 },
	{ name = "static 3x3 lazy", width = 3, totalLayers = 3, staticFraction = 1, nSources = 2, readFraction = 2 / 3, iterations = 10, expected = 72, floor = 41 },
	{ name = "dynamic 4x2", width = 4, totalLayers = 2, staticFraction = 0.5, nSources = 2, readFraction = 1, iterations = 10, expected = 72, floor = 22 },
}

local M: Types.Suite = { cases = {}, checks = {} }

table.insert(M.checks, {
	name = "simple dependency",
	scale = false,
	run = function(bridge: Framework)
		harness.withCleanup(bridge, function()
			bridge.withBuild(function()
				local s = bridge.signal(2)
				local c = bridge.computed(function()
					return s.read() * 2
				end)
				harness.expect(c.read() == 4, "simple dependency initial")
				bridge.withBatch(function()
					s.write(5)
				end)
				harness.expect(c.read() == 10, "simple dependency updated")
				return nil
			end)
			return nil
		end)
	end,
})

local function addCheck(name: string, config: Config, scale: boolean)
	table.insert(M.checks, {
		name = name,
		scale = scale,
		run = function(bridge: Framework)
			local sum, pulls = 0, 0
			harness.withCleanup(bridge, function()
				local graph = makeGraph(bridge, config)
				sum = runGraph(graph, config.iterations, bridge, true)
				pulls = graph.counter.count
				return nil
			end)
			expectSum(sum, config)
			if config.floor then
				harness.expectFloor(name, pulls, config.floor)
			end
		end,
	})
end

for _, config in tests do
	addCheck(config.name or title(config), config, false)
end

for _, config in configs do
	local name = title(config)
	table.insert(M.cases, {
		name = name,
		run = function(bridge: Framework): number
			local sum = 0
			local secs = harness.withCleanup(bridge, function()
				local graph = makeGraph(bridge, config)
				for _ = 1, 3 do -- upstream's warm-up runs
					runGraph(graph, config.iterations, bridge, true)
				end
				return harness.fastest(5, function()
					local t0 = os.clock()
					sum = runGraph(graph, config.iterations, bridge, true)
					return os.clock() - t0
				end)
			end)
			expectSum(sum, config) -- upstream verifies the timed runs' sum; every run ends on the same one
			return secs
		end,
		-- one steady-state run: the first after a build writes one source its current value
		evals = function(bridge: Framework): number
			local computed, effect = harness.count(bridge, function(counted, reset)
				local graph = makeGraph(counted, config)
				runGraph(graph, config.iterations, counted, false)
				reset()
				runGraph(graph, config.iterations, counted, false)
			end)
			return computed + effect
		end,
	})
	addCheck(name, config, true)
end

return M
