-- Entry point. benchmark.py runs one process per row and reads the JSON line it prints:
--
--   luau main.luau -a meta                       { benches = {{ key, cap, evals }}, checks = { key } }
--   luau main.luau -a probe  <adapter>           true if it loads as a framework
--   luau main.luau -a bench  <adapter> <index>   { status, seconds?, detail? }
--   luau main.luau -a evals  <adapter> <index>   node evaluations per timed unit, or null
--   luau main.luau -a check  <adapter> <index>   { status, detail? }
--   luau main.luau -a memory <adapter>           gc.memory's footprint and retention figures
--
-- status is "ok", "overflow", "table-overflow", "timeout", "wrong" or "error".

local harness = require("./harness")
local gc = require("./benches/gc")

local suites = {
	{ "kairo", require("./benches/kairo") },
	{ "cellx", require("./benches/cellx") },
	{ "dynamic", require("./benches/dynamic") },
	{ "sbench", require("./benches/sbench") },
	{ "gc", gc },
}

-- `cap` is a DNF's charge: the deadline in the row's own units (kairo reports per iteration).
local benches, checks = {}, {}
for _, suite in suites do
	local name, module = suite[1], suite[2]
	for _, case in module.cases do
		local cap = harness.DEADLINE / (case.per or 1)
		table.insert(benches, { key = `{name}/{case.name}`, cap = cap, run = case.run, evals = case.evals })
	end
	for _, check in module.checks do
		table.insert(checks, { key = `{name}/{check.name}`, run = check.run })
	end
end

-- A harness failure carries its status; of a framework's own errors only the VM's overflows are told apart.
local function classify(err: any): string
	if type(err) == "table" and err.status then
		return err.status
	end
	local s = tostring(err)
	if string.find(s, "stack overflow", 1, true) then
		return "overflow"
	elseif string.find(s, "table overflow", 1, true) then
		return "table-overflow"
	end
	return "error"
end

local function row(fn, bridge)
	local ok, res = pcall(fn, bridge)
	if ok then
		return { status = "ok", seconds = res }
	end
	return { status = classify(res), detail = tostring(res) }
end

local command, adapter, index = ...
local bridge = adapter and require("../adapters/" .. adapter)
local i = tonumber(index)
local result

if command == "meta" then
	local keys = {}
	for n, c in checks do
		keys[n] = c.key
	end
	result = { benches = {}, checks = keys }
	for n, b in benches do
		result.benches[n] = { key = b.key, cap = b.cap, evals = b.evals ~= nil }
	end
elseif command == "probe" then
	result = type(bridge) == "table" and type(bridge.withBuild) == "function"
elseif command == "bench" then
	result = row(benches[i].run, bridge)
elseif command == "evals" then
	local ok, evals = pcall(benches[i].evals, bridge)
	result = if ok then evals else nil
elseif command == "check" then
	result = row(checks[i].run, bridge)
	result.seconds = nil
elseif command == "memory" then
	result = gc.memory(bridge)
else
	error(`unknown command {command}`)
end

print(harness.json(result))
