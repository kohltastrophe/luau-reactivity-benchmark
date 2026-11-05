--!strict

local Types = require("./types")
local charm = require("@charm")

-- Pin studio-only debug checks off to match Roblox production builds.
charm.flags.strict = false
charm.flags.frozen = false

type Computed = Types.Computed
type Signal = Types.Signal
type Framework = Types.Framework

local signal = charm.signal
local computed = charm.computed
local effect = charm.effect
local batch = (charm.batch :: unknown) :: (fn: () -> ()) -> ()
local effectScope = charm.effectScope

local destroy: (() -> ())? = nil

local function cleanup()
	local d = destroy
	destroy = nil
	if d then
		d()
	end
end

local M: Framework = {
	-- charm.signal's getter/setter pair, which charm.atom wraps in one vararg function
	signal = function(initial: any): Signal
		local get, set = signal(initial)
		return { read = get, write = set }
	end,
	computed = function(fn: () -> any): Computed
		return { read = computed(fn) }
	end,
	-- charm registers any truthy return value as a cleanup callback, so discard fn()'s return
	effect = function(fn: () -> ...any)
		effect(function(): nil
			fn()
			return nil
		end)
	end,
	withBatch = batch,
	withBuild = function<T>(fn: () -> T): T
		if destroy then
			cleanup()
		end
		local ok = false
		local result: any = nil
		destroy = effectScope(function(): nil
			ok, result = pcall(fn)
			return nil
		end)
		if not ok then
			cleanup()
			error(result, 0)
		end
		return result
	end,
	cleanup = cleanup,
}

return M
