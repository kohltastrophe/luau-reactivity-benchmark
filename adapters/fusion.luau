--!strict

local Types = require("./types")
-- Require submodules directly because the package root loads RobloxExternal, which requires `game`.
local FusionTypes = require("@fusion/Types")
local External = require("@fusion/External")
local Value = require("@fusion/State/Value")
local Computed = require("@fusion/State/Computed")
local Observer = require("@fusion/Graph/Observer")
local peek = require("@fusion/State/peek")
local scoped = require("@fusion/Memory/scoped")
local doCleanup = require("@fusion/Memory/doCleanup")

-- Fusion aborts invalidation walks after ~1s assuming a cycle. Deep lattices revisit nodes
-- enough times to trip that timer, so relax it to let acyclic graphs run to completion.
External.safetyTimerMultiplier = 1e9

local function throwNonFatal(errorString: string): ()
	error(errorString, 0)
end

local function noopWarn(_errorString: string) end

local function runImmediate(resume: () -> ())
	resume()
end

local function noop() end

-- Synchronous scheduler stub. Non-fatal errors throw so broken bench bodies fail the run.
local provider: FusionTypes.ExternalProvider = {
	policies = { allowWebLinks = false },
	logErrorNonFatal = throwNonFatal,
	logWarn = noopWarn,
	doTaskImmediate = runImmediate,
	doTaskDeferred = runImmediate,
	startScheduler = noop,
	stopScheduler = noop,
}
External.setExternalProvider(provider)

type ComputedNode = Types.Computed
type Signal = Types.Signal
type Framework = Types.Framework

local scope: FusionTypes.Scope<{}>? = nil
local currentUse: FusionTypes.Use? = nil

local function getScope(): FusionTypes.Scope<{}>
	local s = scope
	if s then
		return s
	end
	local created = scoped()
	scope = created
	return created
end

local function readState(node: FusionTypes.StateObject<any>): any
	if currentUse then
		return currentUse(node)
	end
	return peek(node)
end

-- Wrap a computed callback so its `node.read()` calls subscribe through Fusion's `use`.
local function tracked(fn: () -> any): (FusionTypes.Use, FusionTypes.Scope<{}>) -> any
	return function(use: FusionTypes.Use): any
		local prev = currentUse
		currentUse = use
		local res = fn()
		currentUse = prev
		return res
	end
end

local function cleanup() -- Fusion errors on cleaning a scope twice
	currentUse = nil
	local s = scope
	scope = nil
	if s then
		doCleanup(s)
	end
end

local M: Framework = {
	signal = function(initial: any): Signal
		local node = Value(getScope(), initial)
		return {
			read = function()
				return readState(node)
			end,
			write = function(v: any)
				node:set(v)
			end,
		}
	end,
	computed = function(fn: () -> any): ComputedNode
		local node = Computed(getScope(), tracked(fn))
		return {
			read = function()
				return readState(node)
			end,
		}
	end,
	-- Fusion has no standalone effect primitive, so pair a Computed with an eager Observer.
	-- Returning an incrementing run count forces Fusion to treat each run as a new value.
	effect = function(fn: () -> ...any)
		local s = getScope()
		local runs = 0
		Observer(
			s,
			Computed(s, function(use: FusionTypes.Use)
				local prev = currentUse
				currentUse = use
				fn()
				currentUse = prev
				runs += 1
				return runs
			end)
		)
	end,
	withBatch = function(fn: () -> ())
		fn()
	end,
	withBuild = function<T>(fn: () -> T): T
		if scope then
			cleanup()
		end
		currentUse = nil
		scope = scoped()
		local ok, res = pcall(fn)
		if not ok then
			cleanup()
			error(res, 0)
		end
		return res
	end,
	cleanup = cleanup,
}

return M
