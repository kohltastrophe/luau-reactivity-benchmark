local Types = require("./types")
-- Individual modules via the @fusion alias: the package root wires up RobloxExternal, which needs `game`.
local External = require("@fusion/External")
local Value = require("@fusion/State/Value")
local Computed = require("@fusion/State/Computed")
local Observer = require("@fusion/Graph/Observer")
local peek = require("@fusion/State/peek")
local scoped = require("@fusion/Memory/scoped")
local doCleanup = require("@fusion/Memory/doCleanup")

-- Fusion aborts an invalidation search running longer than ~1s, assuming a cycle. Its search revisits a node
-- once per path to it, so on lattices it grows exponentially and trips the timer; relaxing it (these graphs
-- are acyclic) lets it run on to its real limit, a table overflow, rather than be misreported as a loop.
External.safetyTimerMultiplier = 1e9

-- A synchronous stand-in for Roblox's task scheduler. Non-fatal errors are raised, so a body that throws
-- fails the run instead of leaving a stale value behind.
External.setExternalProvider({
	policies = { allowWebLinks = false },
	logErrorNonFatal = function(errorString: any)
		error(errorString, 0)
	end,
	logWarn = function() end,
	doTaskImmediate = function(resume: () -> ())
		resume()
	end,
	doTaskDeferred = function(resume: () -> ())
		resume()
	end,
	startScheduler = function() end,
	stopScheduler = function() end,
} :: any)

type Node = Types.Node
type Framework = Types.Framework

local M = {} :: Framework

local scope: any = nil
local currentUse: ((any) -> any)? = nil

local function read(node: any): any
	if currentUse then
		return currentUse(node)
	end
	return peek(node)
end

-- wraps a bench body so its `node.read()` calls subscribe through Fusion's `use`
local function tracked(fn: () -> any): (any) -> any
	return function(use: (any) -> any): any
		local prev = currentUse
		currentUse = use
		local res = fn()
		currentUse = prev
		return res
	end
end

function M.signal(initial: any): Node
	local node = Value(scope, initial)
	return {
		read = function()
			return read(node)
		end,
		write = function(v)
			node:set(v)
		end,
	}
end

function M.computed(fn: () -> any): Node
	local node = Computed(scope, tracked(fn))
	return {
		read = function()
			return read(node)
		end,
	}
end

-- Fusion has no user-facing effect, so one is a Computed running the body plus an eager Observer, which
-- evaluates it on construction and whenever it's invalidated. The Computed returns a fresh count: Fusion
-- only re-runs a node whose dependencies changed after it last did, so one always returning nil would
-- never count as changed and would re-run on every later invalidation.
function M.effect(fn: () -> ())
	local runs = 0
	local body = tracked(fn :: any)
	Observer(
		scope,
		Computed(scope, function(use)
			body(use)
			runs += 1
			return runs
		end)
	)
end

function M.withBatch(fn: () -> ())
	fn()
end

function M.withBuild<T>(fn: () -> T): T
	scope = scoped()
	return fn()
end

function M.cleanup() -- Fusion errors on cleaning a scope twice
	local s = scope
	scope = nil
	if s then
		doCleanup(s)
	end
end

return M
