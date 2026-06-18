local PATH = "../lib/vide/"

local Types = require("./types")
local flags = require(`{PATH}flags`)
local vide = {
	source = require(`{PATH}source`),
	derive = require(`{PATH}derive`),
	effect = require(`{PATH}effect`),
	batch = require(`{PATH}batch`),
	root = require(`{PATH}root`),
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

local destroy: () -> ()

function M.withBuild<T>(fn: () -> T): T
	local result: any = nil
	destroy = vide.root(function()
		result = fn()
	end)
	return result
end

function M.cleanup()
	destroy()
end

return M
