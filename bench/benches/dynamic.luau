-- Port of the reactively "dynamic graph" benchmark from milomg/js-reactivity-benchmark.

local harness = require("../harness")

local M = {}

M.timeCap = 8.0

local function makeRow(sources, staticFraction, nSources, bridge, random)
	local row = {}
	for myDex = 1, #sources do
		local mySources = {}
		for sourceDex = 0, nSources - 1 do
			mySources[sourceDex + 1] = sources[((myDex - 1 + sourceDex) % #sources) + 1]
		end

		local isStatic = random() < staticFraction
		if isStatic then
			row[myDex] = bridge.computed(function()
				local sum = 0
				for _, src in mySources do
					sum += src.read()
				end
				return sum
			end)
		else
			local first = mySources[1]
			local tail = {}
			for i = 2, #mySources do
				tail[i - 1] = mySources[i]
			end
			row[myDex] = bridge.computed(function()
				local sum = first.read()
				local shouldDrop = sum % 2 == 1
				local dropDex = (#tail > 0) and (sum % #tail) or 0
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

local function removeElems(src, rmCount, rand)
	local copy = table.clone(src)
	for _ = 1, rmCount do
		local rmDex = math.floor(rand() * #copy) + 1
		table.remove(copy, rmDex)
	end
	return copy
end

function M.makeGraph(bridge, config)
	local width = config.width
	local totalLayers = config.totalLayers
	local staticFraction = config.staticFraction
	local nSources = config.nSources
	local readFraction = config.readFraction

	return bridge.withBuild(function()
		local sources = {}
		for i = 1, width do
			sources[i] = bridge.signal(i - 1)
		end

		local rows = {}
		local prevRow = sources
		local nodeRand = harness.rng(0x12345)
		for _ = 1, totalLayers - 1 do
			local row = makeRow(prevRow, staticFraction, nSources, bridge, nodeRand)
			table.insert(rows, row)
			prevRow = row
		end

		local leaves = rows[#rows]
		local leafRand = harness.rng(0x6789a)
		local skipCount = math.floor(#leaves * (1 - readFraction) + 0.5)
		local readLeaves = removeElems(leaves, skipCount, leafRand)

		bridge.effect(function()
			for _, leaf in readLeaves do
				leaf.read()
			end
		end)

		return { graph = { sources = sources, layers = rows, readLeaves = readLeaves } }
	end)
end

function M.runGraph(graph, iterations, bridge, timeout: number?): number
	local sources = graph.sources
	local readLeaves = graph.readLeaves
	local n = #sources
	for i = 0, iterations - 1 do
		local sourceDex = i % n
		bridge.withBatch(function()
			sources[sourceDex + 1].write(i + sourceDex)
		end)
		for _, leaf in readLeaves do
			leaf.read()
		end
		if timeout and os.clock() > timeout then
			error("dynamic deadline exceeded", 0)
		end
	end
	local sum = 0
	for _, leaf in readLeaves do
		sum += leaf.read()
	end
	return sum
end

local function percent(n: number): string
	return tostring(math.floor(n * 100 + 0.5)) .. "%"
end

function M.title(config): string
	local dyn = if config.staticFraction < 1 then " - dyn" .. percent(1 - config.staticFraction) else ""
	local read = if config.readFraction < 1 then " - lazy" .. percent(1 - config.readFraction) else ""
	return string.format("%d-%dx%d%s%s", config.nSources, config.width, config.totalLayers, dyn, read)
end

M.configs = {
	{ width = 10, totalLayers = 5, staticFraction = 1, nSources = 2, readFraction = 0.2, iterations = 12000 },
	{ width = 10, totalLayers = 10, staticFraction = 3 / 4, nSources = 6, readFraction = 0.2, iterations = 2000 },
	{ width = 1000, totalLayers = 12, staticFraction = 0.95, nSources = 4, readFraction = 1, iterations = 2000 },
	{ width = 1000, totalLayers = 5, staticFraction = 1, nSources = 25, readFraction = 1, iterations = 2000 },
	{ width = 5, totalLayers = 500, staticFraction = 1, nSources = 3, readFraction = 1, iterations = 300 },
	{ width = 100, totalLayers = 15, staticFraction = 0.5, nSources = 6, readFraction = 1, iterations = 1000 },
}

return M
