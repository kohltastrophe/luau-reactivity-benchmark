local Types = require("./types")

local path = "../lib/charm/"
local charm = {
	atom = require(`{path}atom`),
	computed = require(`{path}computed`),
	effect = require(`{path}effect`),
	batch = require(`{path}store`).batch,
} :: any

type Node = Types.Node
type Framework = Types.Framework

local M = {} :: Framework

-- doesn't dedupe nodes during traversal, so its cost grows super-linearly
-- M.DNF = {
-- 	["cellx/cellx1000"] = "exponential blowup",
-- 	["cellx/cellx2500"] = "exponential blowup",
-- 	["dynamic/6-10x10 - dyn25% - lazy80%"] = "exponential blowup",
-- 	["dynamic/4-1000x12 - dyn5%"] = "exponential blowup",
-- 	["dynamic/25-1000x5"] = "exponential blowup",
-- 	["dynamic/3-5x500"] = "exponential blowup",
-- 	["dynamic/6-100x15 - dyn50%"] = "exponential blowup",
-- }

function M.signal(initial: any): Node
	local a = charm.atom(initial)
	return {
		read = function()
			return a()
		end,
		write = function(v)
			a(v)
		end,
	}
end

function M.computed(fn: () -> any): Node
	local c = charm.computed(fn)
	return {
		read = function()
			return c()
		end,
	}
end

local effects: { () -> () } = {}

function M.effect(fn: () -> ())
	table.insert(effects, charm.effect(function()
		fn()
	end))
end

function M.withBatch(fn: () -> ())
	charm.batch(fn)
end

function M.withBuild<T>(fn: () -> T): T
	return fn()
end

function M.cleanup()
	for _, dispose in effects do
		dispose()
	end
	table.clear(effects)
end

return M
