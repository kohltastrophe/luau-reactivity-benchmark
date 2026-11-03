--!strict

local Types = require("./types")
-- Require submodules directly via @vide: the package root (init.luau) is Vide's Roblox UI entry point.
local flags = require("@vide/flags")
local source = require("@vide/source")
local derive = require("@vide/derive")
local effect = require("@vide/effect")
local batch = require("@vide/batch")
local root = require("@vide/root")

-- Match Vide's production build (disables double-invocation strict checks).
flags.strict = false

type Computed = Types.Computed
type Signal = Types.Signal
type Framework = Types.Framework

local videEffect = (effect :: unknown) :: (fn: () -> ...any) -> ()
local destroy: (() -> ())? = nil

local function cleanup() -- Vide errors if a root is destroyed twice
	local d = destroy
	destroy = nil
	if d then
		d()
	end
end

local M: Framework = {
	signal = function(initial: any): Signal
		local src = source(initial)
		return { read = src, write = src }
	end,
	computed = function(fn: () -> any): Computed
		return { read = derive(fn) }
	end,
	effect = videEffect,
	withBatch = batch,
	withBuild = function<T>(fn: () -> T): T
		if destroy then
			cleanup()
		end
		local d, result = root(function()
			return fn()
		end)
		destroy = d
		return result
	end,
	cleanup = cleanup,
}

return M
