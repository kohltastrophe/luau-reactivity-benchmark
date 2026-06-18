local Types = require("./types")
local Flux = require("@self/Graph")

type Node = Types.Node
type Framework = Types.Framework

-- strict is forced off to match Flux's production build
Flux.strict(false)

local M = {} :: Framework

function M.signal(initial: any): Node
	local node = Flux.signal(initial)
	return {
		read = function()
			return node()
		end,
		write = function(v)
			node(v)
		end,
	}
end

function M.computed(fn: () -> any): Node
	local node = Flux.computed(fn)
	return {
		read = function()
			return node()
		end,
	}
end

function M.effect(fn: () -> ())
	Flux.effect(fn)
end

function M.withBatch(fn: () -> ())
	fn()
	Flux.flush()
end

local dispose: any?

function M.withBuild<T>(fn: () -> T): T
	local result: any = nil
	dispose = Flux.scope(function()
		result = fn()
	end)
	return result
end

function M.cleanup()
	if dispose then
		dispose()
		dispose = nil
	end
end

return M
