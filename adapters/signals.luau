-- Off Roblox `game:DefineFastFlag` throws and SignalsFlags' fallback turns every flag on, which
-- would wrap each batch and effect re-run in xpcall + debug.traceback. Pin them to the defaults they
-- ship with on Roblox (all off) before the scheduler loads, since it copies its flag at load time.
local flags = require("@signals.SignalsFlags")
for name in flags do
	flags[name] = false
end

-- signals is a rojo monorepo: its modules are mounted beside the package root, so they're reached
-- through the @signals.<Module> aliases benchmark.py writes for each mount
local Signals = require("@signals.Signals")
local batch = require("@signals.SignalsScheduler").batch

local createSignal, createComputed, createEffect = Signals.createSignal, Signals.createComputed, Signals.createEffect

local M = {}

-- signals tracks reads through the scope it passes each computed/effect body; the bench bodies
-- read via node.read(), so the active scope is kept here. nil = untracked read.
local currentScope: any = nil

local function scoped(fn: () -> any): (any) -> any
	return function(scope)
		local prev = currentScope
		currentScope = scope
		local res = fn()
		currentScope = prev
		return res
	end
end

function M.signal(initial: any)
	local getter, setter = createSignal(initial)
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
	local getter = createComputed(scoped(fn))
	return {
		read = function()
			return getter(currentScope)
		end,
	}
end

-- signals holds observers in weak sets, so the dispose handle is the only strong ref keeping an effect
-- alive; without it effects would be collected mid-run and silently stop re-running.
local effects: { any } = {}

function M.effect(fn: () -> ())
	table.insert(effects, createEffect(scoped(fn)))
end

function M.withBatch(fn: () -> ())
	batch(fn)
end

function M.withBuild<T>(fn: () -> T): T
	return fn()
end

function M.cleanup()
	for _, dispose in effects do
		dispose()
	end
	effects = {}
end

return M
