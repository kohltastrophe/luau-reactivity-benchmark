local Types = require("./types")
-- individual modules via the @vide alias: the package root (vide's init.luau) is its Roblox UI entry point
local flags = require("@vide/flags")
local vide = {
	source = require("@vide/source"),
	derive = require("@vide/derive"),
	effect = require("@vide/effect"),
	batch = require("@vide/batch"),
	root = require("@vide/root"),
} :: any

-- strict is forced off to match Vide's production build
flags.strict = false

type Node = Types.Node
type Framework = Types.Framework

local M = {} :: Framework

function M.signal(initial: any): Node
	local src = vide.source(initial)
	return {
		read = function()
			return src()
		end,
		write = function(v)
			src(v)
		end,
	}
end

function M.computed(fn: () -> any): Node
	local d = vide.derive(fn)
	return {
		read = function()
			return d()
		end,
	}
end

function M.effect(fn: () -> ())
	vide.effect(fn)
end

function M.withBatch(fn: () -> ())
	vide.batch(fn)
end

local destroy: (() -> ())? = nil

function M.withBuild<T>(fn: () -> T): T
	local result: any = nil
	destroy = vide.root(function()
		result = fn()
	end)
	return result
end

function M.cleanup() -- vide errors on destroying a root twice
	local d = destroy
	destroy = nil
	if d then
		d()
	end
end

return M
