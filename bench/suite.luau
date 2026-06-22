local harness = require("./harness")
local kairo = require("./benches/kairo")
local cellx = require("./benches/cellx")
local dynamic = require("./benches/dynamic")
local sbench = require("./benches/sbench")
local gc = require("./benches/gc")

local clock = os.clock
local KREP = 50

local function classify(err: any): string
	local s = tostring(err)
	if string.find(s, "stack overflow", 1, true) then
		return "overflow"
	end
	if string.find(s, "table overflow", 1, true) then
		return "table-overflow"
	end
	if string.find(s, "deadline", 1, true) then
		return "timeout"
	end
	return "error"
end

local function fastest(repeats, fn): (number?, string)
	harness.gc()
	local ok, res = pcall(fn)
	if not ok then
		return nil, classify(res)
	end
	local best = res :: number
	for _ = 2, repeats do
		local ok2, t = pcall(fn)
		if not ok2 then
			return nil, classify(t)
		end
		if (t :: number) < best then
			best = t :: number
		end
	end
	return best, "ok"
end

local benches: { { key: string, cap: number?, run: (any) -> (number?, string) } } = {}

for _, case in kairo do
	table.insert(benches, {
		key = "kairo/" .. case.name,
		run = function(bridge)
			local iter = bridge.withBuild(function()
				return case.fn(bridge)
			end)
			iter(false)
			iter(true) -- verify values
			local t, st = fastest(6, function()
				local t0 = clock()
				for _ = 1, KREP do
					iter(false)
				end
				return clock() - t0
			end)
			bridge.cleanup()
			return if t then t / KREP else nil, st
		end,
	})
end

table.insert(benches, {
	key = "cellx/cellx1000",
	run = function(bridge)
		return fastest(5, function()
			local iter = cellx.build(bridge, 1000)
			harness.gc()
			local t0 = clock()
			iter()
			local dt = clock() - t0
			bridge.cleanup()
			return dt
		end)
	end,
})

table.insert(benches, {
	key = "cellx/cellx2500",
	run = function(bridge)
		return fastest(5, function()
			local iter = cellx.build(bridge, 2500)
			harness.gc()
			local t0 = clock()
			iter()
			local dt = clock() - t0
			bridge.cleanup()
			return dt
		end)
	end,
})

for _, config in dynamic.configs do
	table.insert(benches, {
		key = "dynamic/" .. dynamic.title(config),
		cap = dynamic.timeCap, -- each run self-deadlines here; a DNF means it hit this budget
		run = function(bridge)
			local built = dynamic.makeGraph(bridge, config)
			local t, st = fastest(5, function()
				local t0 = clock()
				dynamic.runGraph(built.graph, config.iterations, bridge, clock() + dynamic.timeCap)
				return clock() - t0
			end)
			bridge.cleanup()
			return t, st
		end,
	})
end

local function selfTimed(suite, cases)
	for _, case in cases do
		table.insert(benches, {
			key = suite .. "/" .. case.name,
			run = function(bridge): (number?, string)
				local ok, secs = pcall(case.fn, bridge)
				if not ok then
					return nil, classify(secs)
				end
				return secs, "ok"
			end,
		})
	end
end

selfTimed("sbench", sbench.cases)
selfTimed("gc", gc.cases)

-- ---------------------------------------------------------------------------
-- Correctness emit (the verify gate's per-unit JSON; consumed by benchmark.py --verify).
-- Each unit runs one graph in this process and prints a single JSON object.
-- ---------------------------------------------------------------------------

local function jstr(s: string): string
	s = s:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t")
	return '"' .. s .. '"'
end

local function numarr(a: { number }): string
	local parts = {}
	for i, v in a do
		parts[i] = string.format("%.0f", v)
	end
	return "[" .. table.concat(parts, ",") .. "]"
end

local GOLDEN = { width = 3, totalLayers = 3, staticFraction = 1, nSources = 2, readFraction = 1, iterations = 2 }

local M = {}
M.count = #benches

function M.emit(bridge: any, unit: string): string
	if unit == "meta" then
		local titles = {}
		for i, cfg in dynamic.configs do
			titles[i] = jstr(dynamic.title(cfg))
		end
		local dnfKeys = {}
		if bridge.DNF then
			for key in bridge.DNF do
				dnfKeys[#dnfKeys + 1] = jstr(key)
			end
		end
		return string.format(
			'{"unit":"meta","dynCount":%d,"dynTitles":[%s],"dnfKeys":[%s]}',
			#dynamic.configs,
			table.concat(titles, ","),
			table.concat(dnfKeys, ",")
		)
	elseif unit == "kairo" then
		local fails = {}
		for _, case in kairo do
			local ok, err = pcall(function()
				local iter = bridge.withBuild(function()
					return case.fn(bridge)
				end)
				iter(false)
				iter(true)
				bridge.cleanup()
			end)
			if not ok then
				fails[#fails + 1] = jstr(string.format("kairo/%s: %s", case.name, tostring(err):sub(1, 110)))
			end
		end
		return string.format('{"unit":"kairo","fails":[%s]}', table.concat(fails, ","))
	elseif unit == "cellx1000" then
		local ok, before, after = pcall(function()
			local iter = cellx.build(bridge, 1000)
			local b, a = iter()
			bridge.cleanup()
			return b, a
		end)
		if ok then
			return string.format('{"unit":"cellx1000","before":%s,"after":%s}', numarr(before), numarr(after))
		end
		return string.format('{"unit":"cellx1000","dnf":true,"err":%s}', jstr(tostring(before):sub(1, 80)))
	elseif unit == "dyn-golden" then
		local ok, sum = pcall(function()
			local built = dynamic.makeGraph(bridge, GOLDEN)
			local s = dynamic.runGraph(built.graph, GOLDEN.iterations, bridge)
			bridge.cleanup()
			return s
		end)
		if ok then
			return string.format('{"unit":"dyn-golden","sum":%.0f}', sum)
		end
		return string.format('{"unit":"dyn-golden","dnf":true,"err":%s}', jstr(tostring(sum):sub(1, 80)))
	elseif unit == "gcmem" then
		local ok, st = pcall(gc.stats, bridge)
		if ok then
			return string.format(
				'{"unit":"gcmem","N":%d,"liveKB":%.1f,"retainedKB":%.1f,"bytesPerNode":%.1f}',
				st.N,
				st.liveKB,
				st.retainedKB,
				st.bytesPerNode
			)
		end
		return string.format('{"unit":"gcmem","dnf":true,"err":%s}', jstr(tostring(st):sub(1, 80)))
	else
		local i = tonumber(unit:match("^dyn:(%d+)$"))
		assert(i and dynamic.configs[i], "suite.emit: bad unit " .. unit)
		local cfg = dynamic.configs[i]
		local ok, sum = pcall(function()
			local built = dynamic.makeGraph(bridge, cfg)
			local s = dynamic.runGraph(built.graph, cfg.iterations, bridge, os.clock() + dynamic.timeCap)
			bridge.cleanup()
			return s
		end)
		if ok then
			return string.format('{"unit":"dyn","i":%d,"sum":%.0f}', i, sum)
		end
		return string.format('{"unit":"dyn","i":%d,"dnf":true,"err":%s}', i, jstr(tostring(sum):sub(1, 80)))
	end
end

M.keys = {}
for i, b in benches do
	M.keys[i] = b.key
end

export type Row = { framework: string, test: string, seconds: number?, status: string, cap: number? }

-- `name` is the adapter's folder name, supplied by benchmark.py; it labels each row.
function M.run(bridge, name: string, i: number): Row
	local bench = benches[i]
	if bridge.DNF then
		local reason = bridge.DNF[bench.key]
		if reason then
			return { framework = name, test = bench.key, seconds = nil, status = reason, cap = bench.cap }
		end
	end
	local ok, secs, status = pcall(bench.run, bridge)
	if not ok then
		secs, status = nil, classify(secs)
	end
	return { framework = name, test = bench.key, seconds = secs, status = status :: string, cap = bench.cap }
end

function M.printRow(bridge, name: string, i: number)
	local r = M.run(bridge, name, i)
	local s = if r.seconds then string.format("%.9f", r.seconds) else "null"
	local status = r.status:gsub("\\", "\\\\"):gsub('"', '\\"')
	local cap = if r.cap then string.format(',"cap":%.6f', r.cap) else ""
	print(
		string.format(
			'__ROW_JSON__{"framework":"%s","test":"%s","seconds":%s,"status":"%s"%s}',
			r.framework,
			r.test,
			s,
			status,
			cap
		)
	)
end

return M
