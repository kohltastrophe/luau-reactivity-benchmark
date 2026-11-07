--!strict

local Types = require("./types")

-- Outside Roblox, `game:DefineFastFlag` throws and SignalsFlags enables debug flags by default,
-- wrapping every batch and effect in `xpcall` + `debug.traceback`. Reset them to `false` to match Roblox.
local flags = require("@signals.SignalsFlags")
flags.SignalsReactUseMutableSource = false
flags.SignalsSchedulerResetStateAfterErrors = false

local Signals = require("@signals.Signals")
local batch = require("@signals.SignalsScheduler").batch

type Computed = Types.Computed
type Signal = Types.Signal
type Framework = Types.Framework

local createSignal = Signals.createSignal
local createComputed = Signals.createComputed
local createEffect = Signals.createEffect

-- signals passes a scope argument into computed/effect callbacks for dependency tracking.
-- Store the active scope so `node.read()` can forward it.
local currentScope: Signals.scope? = nil
-- Keep dispose handles alive per build because signals stores observers in weak tables.
local effects: { Signals.dispose }? = nil

local function scoped<T>(fn: () -> T): (Signals.scope) -> T
	return function(scope: Signals.scope): T
		local prev = currentScope
		currentScope = scope
		local res = fn()
		currentScope = prev
		return res
	end
end

local function scopedEffect(fn: () -> ...any): (Signals.scope) -> ()
	return function(scope: Signals.scope)
		local prev = currentScope
		currentScope = scope
		fn()
		currentScope = prev
	end
end

local function cleanup()
	currentScope = nil
	local list = effects
	effects = nil
	if list then
		for _, dispose in list do
			dispose()
		end
	end
end

local M: Framework = {
	signal = function(initial: any): Signal
		local getter, setter = createSignal(initial)
		return {
			read = function()
				return getter(currentScope)
			end,
			write = setter,
		}
	end,
	computed = function(fn: () -> any): Computed
		local getter = createComputed(scoped(fn))
		return {
			read = function()
				return getter(currentScope)
			end,
		}
	end,
	effect = function(fn: () -> ...any)
		local list = effects
		if list then
			table.insert(list, createEffect(scopedEffect(fn)))
		else
			local created: { Signals.dispose } = { createEffect(scopedEffect(fn)) }
			effects = created
		end
	end,
	withBatch = batch,
	withBuild = function<T>(fn: () -> T): T
		if effects then
			cleanup()
		end
		currentScope = nil
		effects = {}
		local ok, res = pcall(fn)
		if not ok then
			cleanup()
			error(res, 0)
		end
		return res
	end,
	cleanup = cleanup,
}

return M
