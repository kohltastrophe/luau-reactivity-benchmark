local path = "../lib/signals/"
local Signals = require(`{path}Signals`)
local Scheduler = require(`{path}SignalsScheduler`)
local S = {
	createSignal = Signals.createSignal,
	createComputed = Signals.createComputed,
	createEffect = Signals.createEffect,
	batch = Scheduler.batch,
	flush = Scheduler.flush,
}
local batch = S.batch

local M = {}

local currentScope: any = nil -- nil/false => untracked read

function M.signal(initial: any)
	local getter, setter = S.createSignal(initial)
	return {
		read = function()
			return getter(currentScope)
		end,
		write = function(v)
			setter(v)
		end,
	}
end

function M.computed(fn: () -> any)
	local getter = S.createComputed(function(scope)
		local prev = currentScope
		currentScope = scope
		local res = fn()
		currentScope = prev
		return res
	end)
	return {
		read = function()
			return getter(currentScope)
		end,
	}
end

-- signals holds observers in WEAK sets; the dispose handle is the only strong ref to an
-- effect's internals. Retain disposes so effects aren't GC'd mid-run (which would silently
-- drop re-runs). Cleared on cleanup().
local effects: { any } = {}

function M.effect(fn: () -> ())
	table.insert(effects, S.createEffect(function(scope)
		local prev = currentScope
		currentScope = scope
		fn()
		currentScope = prev
	end))
end

function M.withBatch(fn: () -> ())
	batch(fn)
end

function M.withBuild<T>(fn: () -> T): T
	currentScope = nil -- clear any shim left dangling by a body that threw last build
	return fn()
end

function M.cleanup()
	for _, dispose in effects do
		dispose()
	end
	table.clear(effects)
end

return M
