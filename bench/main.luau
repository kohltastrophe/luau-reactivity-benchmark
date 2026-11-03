--!strict

-- Entry point. benchmark.py runs one process per row and reads the JSON line it prints:
--
--   luau main.luau -a meta                       { deadline, benches = {{ key, cap, evals }}, checks = {{ key, scale }} }
--   luau main.luau -a probe  <adapter>           { ok, detail? }
--   luau main.luau -a bench  <adapter> <index>   { status, seconds?, detail? }
--   luau main.luau -a evals  <adapter> <index>   node evaluations per timed unit, or null
--   luau main.luau -a check  <adapter> <index>   { status, detail? }
--
-- status is "ok", "overflow", "table-overflow", "oom", "timeout", "wrong", or "error".

local Types = require("../adapters/types")
local harness = require("./harness")
local kairo = require("./benches/kairo")
local cellx = require("./benches/cellx")
local dynamic = require("./benches/dynamic")
local sbench = require("./benches/sbench")

type Framework = Types.Framework

local suites: { { name: string, suite: Types.Suite } } = {
	{ name = "kairo", suite = kairo },
	{ name = "cellx", suite = cellx },
	{ name = "dynamic", suite = dynamic },
	{ name = "sbench", suite = sbench },
}

-- Lazy thunks keep static module graph edges for typecheckers/indexers while loading only the requested adapter.
local ADAPTERS: { [string]: () -> Framework } = {
	flux = function()
		return require("../adapters/flux")
	end,
	vide = function()
		return require("../adapters/vide")
	end,
}

local dynamicRequire = (require :: unknown) :: (string) -> Framework

local function loadAdapter(name: string): Framework
	local loader = ADAPTERS[name]
	if loader then
		return loader()
	end
	return dynamicRequire("../adapters/" .. name)
end

type BenchEntry = {
	key: string,
	cap: number,
	run: (bridge: Framework) -> number,
	evals: ((bridge: Framework) -> number)?,
}

type CheckEntry = {
	key: string,
	scale: boolean,
	run: (bridge: Framework) -> (),
}

local function checkContract(bridge: Framework)
	harness.withCleanup(bridge, function()
		local effectRuns = 0
		local lastObserved: any = "unset"
		local built = bridge.withBuild(function()
			local s = bridge.signal(1 :: any)
			local c = bridge.computed(function()
				local v = s.read()
				return if v == nil then -1 else v * 10
			end)
			bridge.effect(function()
				effectRuns += 1
				lastObserved = c.read()
			end)
			return { sig = s, derived = c, token = "built-ok" }
		end)
		harness.expect(built.token == "built-ok", "withBuild did not forward return value")
		harness.expect(effectRuns == 1 and lastObserved == 10, "withBuild did not run initial effect synchronously")

		bridge.withBatch(function()
			built.sig.write(2)
		end)
		harness.expect(effectRuns == 2 and lastObserved == 20, "withBatch did not flush effect synchronously")

		bridge.withBatch(function()
			built.sig.write(nil)
		end)
		harness.expect(built.sig.read() == nil, "signal.write(nil) did not store nil")
		harness.expect(built.derived.read() == -1 and lastObserved == -1, "nil write did not propagate")

		bridge.cleanup()
		bridge.cleanup()
		bridge.withBatch(function()
			built.sig.write(3)
		end)
		harness.expect(effectRuns == 3, "disposed effect ran after cleanup")

		local okBuild = pcall(function()
			bridge.withBuild(function()
				bridge.effect(function()
					error("contract build error", 0)
				end)
				return nil
			end)
		end)
		pcall(bridge.cleanup)
		harness.expect(not okBuild, "effect error inside withBuild did not propagate")

		-- Run withBatch error last: adapters without scheduler state-reset flags may remain latched after a throw.
		local okBatch = pcall(function()
			local trigger = bridge.signal(0)
			bridge.withBuild(function()
				bridge.effect(function()
					if trigger.read() > 0 then
						error("contract batch error", 0)
					end
				end)
				return nil
			end)
			bridge.withBatch(function()
				trigger.write(1)
			end)
		end)
		pcall(bridge.cleanup)
		harness.expect(not okBatch, "effect error inside withBatch did not propagate")
		return nil
	end)
end

-- DNF penalty in the row's own units (kairo reports per iteration).
local benches: { BenchEntry } = {}
local checks: { CheckEntry } = {
	{ key = "contract/lifecycle", scale = false, run = checkContract },
}
for _, entry in suites do
	local name, module = entry.name, entry.suite
	for _, case in module.cases do
		local cap = harness.DEADLINE / (case.per or 1)
		table.insert(benches, { key = `{name}/{case.name}`, cap = cap, run = case.run, evals = case.evals })
	end
	for _, check in module.checks do
		table.insert(checks, { key = `{name}/{check.name}`, scale = check.scale == true, run = check.run })
	end
end

-- Map harness and VM errors to their status strings.
local function classify(err: unknown): string
	if type(err) == "table" then
		local status = (err :: { status: unknown }).status
		if type(status) == "string" then
			return status
		end
	end
	local s = string.lower(tostring(err))
	if string.find(s, "stack overflow", 1, true) or string.find(s, "too many results", 1, true) then
		return "overflow"
	elseif string.find(s, "table overflow", 1, true) then
		return "table-overflow"
	elseif
		string.find(s, "not enough memory", 1, true)
		or string.find(s, "block too big", 1, true)
		or string.find(s, "string length overflow", 1, true)
	then
		return "oom"
	end
	return "error"
end

local function requireIndex<T>(list: { T }, idx: number?, kind: string): T
	if not idx or idx < 1 or idx > #list or idx % 1 ~= 0 then
		error(`invalid {kind} index: {tostring(idx)}`, 0)
	end
	return list[idx]
end

local command, adapter, index = ...
local i = if index ~= nil then tonumber(index) else nil
local result: any

if command == "meta" then
	local metaBenches = table.create(#benches)
	for n, b in benches do
		metaBenches[n] = { key = b.key, cap = b.cap, evals = b.evals ~= nil }
	end
	local metaChecks = table.create(#checks)
	for n, c in checks do
		metaChecks[n] = { key = c.key, scale = c.scale }
	end
	result = { deadline = harness.DEADLINE, benches = metaBenches, checks = metaChecks }
elseif command == "probe" then
	if type(adapter) ~= "string" or adapter == "" then
		result = { ok = false, detail = "missing adapter name" }
	else
		local ok, bridgeOrErr = pcall(loadAdapter, adapter)
		if not ok then
			result = { ok = false, detail = tostring(bridgeOrErr) }
		elseif
			type(bridgeOrErr) ~= "table"
			or type(bridgeOrErr.signal) ~= "function"
			or type(bridgeOrErr.computed) ~= "function"
			or type(bridgeOrErr.effect) ~= "function"
			or type(bridgeOrErr.withBatch) ~= "function"
			or type(bridgeOrErr.withBuild) ~= "function"
			or type(bridgeOrErr.cleanup) ~= "function"
		then
			result = { ok = false, detail = "adapter does not implement the Framework contract" }
		else
			result = { ok = true }
		end
	end
else
	if type(adapter) ~= "string" or adapter == "" then
		error(`missing adapter for command {tostring(command)}`, 0)
	end
	local bridge = loadAdapter(adapter)
	if command == "bench" then
		local entry = requireIndex(benches, i, "bench")
		local ok, res = pcall(entry.run, bridge)
		if ok then
			result = { status = "ok", seconds = res }
		else
			pcall(bridge.cleanup)
			result = { status = classify(res), detail = tostring(res) }
		end
	elseif command == "evals" then
		local entry = requireIndex(benches, i, "evals")
		if entry.evals then
			local ok, evals = pcall(entry.evals, bridge)
			if not ok then
				pcall(bridge.cleanup)
				error(evals, 0)
			end
			result = evals
		else
			result = nil
		end
	elseif command == "check" then
		local entry = requireIndex(checks, i, "check")
		local ok, err = pcall(entry.run :: (Framework) -> ...unknown, bridge)
		if ok then
			result = { status = "ok" }
		else
			pcall(bridge.cleanup)
			result = { status = classify(err), detail = tostring(err) }
		end
	else
		error(`unknown command {tostring(command)}`, 0)
	end
end

print(harness.json(result))
