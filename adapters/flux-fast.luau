local Types = require("./types")
local Flux = require("@flux")

type Node = Types.Node
type Framework = Types.Framework

local createSignal = Flux.createSignal
local createMemo = Flux.createMemo
local createEffect = Flux.createEffect
local flush = Flux.flush
local scopeFn = Flux.scope

local M = {} :: Framework

function M.signal(initial: any): Node
	local get, set = createSignal(initial)
	return {
		read = get,
		write = set,
	}
end

function M.computed(fn: () -> any): Node
	return {
		read = createMemo(fn),
	}
end

M.effect = createEffect

function M.withBatch(fn: () -> ())
	fn()
	flush()
end

local scope: any?

function M.withBuild<T>(fn: () -> T): T
	local s, result = scopeFn(function()
		local r = fn()
		flush()
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
