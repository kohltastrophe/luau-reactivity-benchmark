local Types = require("./types")
local Flux = require("@flux")

type Node = Types.Node
type Framework = Types.Framework

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

local scope: any?

function M.withBuild<T>(fn: () -> T): T
	local s, result = Flux.scope(function()
		local r = fn()
		Flux.flush()
		return r
	end)
	scope = s
	return result
end

function M.cleanup()
	if scope then
		scope:Destroy()
		scope = nil
	end
end

return M
