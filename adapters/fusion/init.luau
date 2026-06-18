local Types = require("./types")
-- Fusion aborts a propagation running longer than ~1s, assuming a cycle; deep acyclic graphs (e.g. cellx at 1000+ layers)
-- can exceed that under its eager-observer model, so relax the timer (these graphs are statically acyclic).
-- A synchronous External provider stands in for Roblox's task scheduler.
local path = "../lib/fusion/"
local External = require(`{path}External`)
External.safetyTimerMultiplier = 1e9

local Value = require(`{path}State/Value`)
local Computed = require(`{path}State/Computed`)
local Observer = require(`{path}Graph/Observer`)
local peek = require(`{path}State/peek`)
local scoped = require(`{path}Memory/scoped`)
local doCleanup = require(`{path}Memory/doCleanup`)

local provider = {
	policies = { allowWebLinks = false },
	logErrorNonFatal = function(errorString: any)
		print("[fusion non-fatal] " .. tostring(errorString))
	end,
	logWarn = function(_errorString: any) end,
	doTaskImmediate = function(resume: () -> ())
		resume()
	end,
	doTaskDeferred = function(resume: () -> ())
		resume()
	end,
	startScheduler = function() end,
	stopScheduler = function() end,
}
External.setExternalProvider(provider :: any)

local F = {
	Value = Value,
	Computed = Computed,
	Observer = Observer,
	peek = peek,
	scoped = scoped,
	doCleanup = doCleanup,
} :: any

type Node = Types.Node
type Framework = Types.Framework

local M = {} :: Framework

-- doesn't dedupe nodes during traversal, so its cost grows super-linearly
-- M.DNF = {
-- 	["cellx/cellx1000"] = "exponential blowup",
-- 	["cellx/cellx2500"] = "exponential blowup",
-- 	["dynamic/3-5x500"] = "exponential blowup",
-- 	["dynamic/6-100x15 - dyn50%"] = "exponential blowup",
-- 	["dynamic/6-10x10 - dyn25% - lazy80%"] = "exponential blowup",
-- 	["dynamic/4-1000x12 - dyn5%"] = "exponential blowup",
-- 	["dynamic/25-1000x5"] = "exponential blowup",
-- }

local scope: any = nil
local currentUse: (any) -> any = nil :: any

local function read(node: any): any
	if currentUse then
		return currentUse(node)
	end
	return F.peek(node)
end

-- wrap a benchmark body so its `node.read()` calls subscribe through Fusion's `use`
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
	local node = F.Value(scope, initial)
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
	local node = F.Computed(scope, tracked(fn))
	return {
		read = function()
			return read(node)
		end,
	}
end

-- Fusion has no user-facing effect; we model one as a Computed running the effect body (so its
-- reads are tracked) plus an eager Observer that re-pulls it on invalidation.
function M.effect(fn: () -> ())
	local node = F.Computed(scope, tracked(fn :: any))
	F.peek(node) -- evaluate once: registers dependencies and runs the baseline
	local observer = F.Observer(scope, node)
	observer:onChange(function()
		F.peek(node)
	end)
end

function M.withBatch(fn: () -> ())
	fn()
end

function M.withBuild<T>(fn: () -> T): T
	currentUse = nil :: any -- clear any shim left dangling by a body that threw last build
	local s = F.scoped()
	local prev = scope
	scope = s
	local ok, result = pcall(fn)
	scope = prev
	M.cleanup = function()
		F.doCleanup(s)
	end
	if not ok then
		error(result, 0)
	end
	return result
end

function M.cleanup() end

return M
