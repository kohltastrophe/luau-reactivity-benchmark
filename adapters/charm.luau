local Types = require("./types")

local charm = require("@charm") :: any

type Node = Types.Node
type Framework = Types.Framework

local M = {} :: Framework

-- charm.signal's getter/setter pair, which charm.atom wraps in one vararg function
function M.signal(initial: any): Node
	local get, set = charm.signal(initial)
	return {
		read = function()
			return get()
		end,
		write = function(v)
			set(v)
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

-- charm registers whatever an effect body returns as its cleanup, so drop the bench body's result
function M.effect(fn: () -> ())
	table.insert(
		effects,
		charm.effect(function()
			fn()
		end)
	)
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
	effects = {} -- not table.clear, which keeps the array capacity alive
end

return M
