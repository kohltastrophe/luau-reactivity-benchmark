--!strict

local Types = require("./types")
local Flux = require("@flux")
local Compat = require("@flux/Compat")

type Computed = Types.Computed
type Signal = Types.Signal
type Framework = Types.Framework

-- Record suppressed effect errors during flush() and raise them once flush() finishes resetting its state.
local pendingError: string? = nil
Compat.setWarnHook(function(msg: string)
	if pendingError == nil then
		pendingError = msg
	end
end)

local createSignal = Flux.createSignal
local createMemo = Flux.createMemo
local createEffect = Flux.createEffect
local flush = Flux.flush
local createScope = (Flux.scope :: unknown) :: <T>(fn: () -> T) -> (Flux.Node<any>, T)

local scope: Flux.Node<any>? = nil
local batchDepth = 0

local function cleanup()
	batchDepth = 0
	pendingError = nil
	local s = scope
	scope = nil
	if s then
		s:Destroy()
	end
end

local function flushChecked()
	flush()
	local err = pendingError
	if err ~= nil then
		batchDepth = 0
		pendingError = nil
		error(err, 0)
	end
end

local M: Framework = {
	signal = function(initial: any): Signal
		local get, set = createSignal(initial)
		return { read = get, write = set }
	end,
	computed = function(fn: () -> any): Computed
		return { read = createMemo(fn) }
	end,
	effect = createEffect,
	withBatch = function(fn: () -> ())
		batchDepth += 1
		fn()
		batchDepth -= 1
		if batchDepth == 0 then
			flushChecked()
		end
	end,
	withBuild = function<T>(fn: () -> T): T
		if scope then
			cleanup()
		end
		batchDepth = 0
		pendingError = nil
		local s, result = createScope(function()
			return fn()
		end)
		scope = s
		if pendingError == nil then
			flush()
		end
		local err = pendingError
		if err ~= nil then
			cleanup()
			error(err, 0)
		end
		return result
	end,
	cleanup = cleanup,
}

return M
