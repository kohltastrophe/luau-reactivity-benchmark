local Types = require("./types")
local Flux = require("../lib/flux")

type Node = Types.Node
type Framework = Types.Framework

-- strict is forced off to match Flux's production build
Flux.strict(false)

local M = {} :: Framework

function M.signal(initial: any): Node
	local node = Flux.signal(initial)
	return {
		read = function()
			return node:get()
		end,
		write = function(v)
			node:set(v)
		end,
	}
end

function M.computed(fn: () -> any): Node
	local node = Flux.computed(fn)
	return {
		read = function()
			return node:get()
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
	local result: any = nil
	scope = Flux.scope(function()
		result = fn()
		Flux.flush()
	end)
	return result
end

function M.cleanup()
	if scope then
		scope:Destroy()
		scope = nil
	end
end

return M
