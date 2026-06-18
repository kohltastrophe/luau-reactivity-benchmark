--!strict


local CLEAN = 0
local CHECK = 1
local DIRTY = 2

local VALUE = 1
local OBSERVERS = 2
local EQUALS = 3
local SEEN = 4
local STATE = 5
local FN = 6
local SOURCES = 7
local CHILDREN = 8
local CLEANUPS = 9
local SCOPE = 10
local EFFECT = 11

local currentReaction: { any }? = nil
local currentScope: { any }? = nil
local currentGets: { any }? = nil
local currentGetsIndex = 0
local currentEvalId = 0
local evalId = 0
local effectQueue: { any } = {}
local effectQueueN = 0
local drainIndex = 0

local warn = warn or print

local function inline_test(): string
	return debug.info(1, "n")
end

local is_O2 = inline_test() ~= "inline_test"

local strict_mode = not is_O2

local Node: { any }

local function strict(on: boolean?): boolean
	if on == nil then
		return strict_mode
	end
	strict_mode = on
	return on
end

local function isNode(object: any): boolean
	return type(object) == "table" and getmetatable(object) == Node
end

local function isReactive(): boolean
	return currentReaction ~= nil
end

local function unwrap(object: any): any
	return if isNode(object) then object[1] else object
end

local function doCleanup(node: { any }, destroyed: boolean)
	local cleanups = node[CLEANUPS]
	if cleanups then
		node[CLEANUPS] = nil
		local oldValue, previousReaction = node[VALUE], currentReaction
		currentReaction = nil
		for _, cleanup in cleanups do
			cleanup(oldValue, destroyed)
		end
		currentReaction = previousReaction
	end
end

local function removeSourceObservers(node: { any }, index: number)
	local sources: { any }? = node[SOURCES]
	if not sources then
		return
	end
	for i = index + 1, #sources do
		local observers: { any } = sources[i][OBSERVERS]
		if observers then
			for j, observer in observers do
				if observer == node then
					observers[j], observers[#observers] = observers[#observers], nil
					break
				end
			end
		end
	end
end

local dispose

local function disposeChildren(node: { any })
	local children = node[CHILDREN]
	if children then
		node[CHILDREN] = nil
		for _, child in children do
			dispose(child)
		end
	end
end

function dispose(node: { any })
	disposeChildren(node)

	if node[EFFECT] then
		node[EFFECT] = nil
	end

	doCleanup(node, true)

	local scope: { any }? = node[SCOPE]
	if scope then
		local owned: { any } = scope[CHILDREN]
		if owned then
			for i, child in owned do
				if child == node then
					owned[i], owned[#owned] = owned[#owned], nil
					break
				end
			end
		end
	end

	local observers = node[OBSERVERS]
	if observers then
		for _, observer in observers do
			local sources = observer[SOURCES]
			if sources then
				for _, source in sources do
					if source == node then
						source, sources[#sources] = sources[#sources], nil
						break
					end
				end
			end
		end
	end

	removeSourceObservers(node, 0)

	table.clear(node)
end


local function enqueueEffect(node: { any })
	if node[EFFECT] then
		effectQueueN += 1
		effectQueue[effectQueueN] = node
	end
end

local function forObservers(node: { any }, fn: (any) -> ())
	local observers: { any }? = node[OBSERVERS]
	if observers then
		for _, observer in observers do
			fn(observer)
		end
	end
end

local function staleCheck(node: { any })
	if node[STATE] == CLEAN then
		enqueueEffect(node)
		node[STATE] = CHECK
		forObservers(node, staleCheck)
	end
end

local function stale(node: { any })
	local state = node[STATE]
	if state < DIRTY then
		if state == CLEAN then
			enqueueEffect(node)
		end
		node[STATE] = DIRTY
		forObservers(node, staleCheck)
	end
end


local function track(node: { any })
	local reaction = currentReaction
	if reaction and node[SEEN] ~= currentEvalId then
		node[SEEN] = currentEvalId
		local gets: { any }? = currentGets
		if gets then
			table.insert(gets, node)
		else
			local index = currentGetsIndex + 1
			local sources: { any }? = reaction[SOURCES]
			if sources and sources[index] == node then
				currentGetsIndex = index
			else
				currentGets = { node }
			end
		end
	end
	return node
end

local function updateSources(node: { any }, gets: { any })
	removeSourceObservers(node, currentGetsIndex)
	local sources: { any } = node[SOURCES]
	if sources and currentGetsIndex > 0 then
		local index = currentGetsIndex
		local newLen = index + #gets
		for i, src in gets do
			sources[index + i] = src
		end
		for i = #sources, newLen + 1, -1 do
			sources[i] = nil
		end
	else
		node[SOURCES], sources = gets, gets
	end
	for i = currentGetsIndex + 1, #sources do
		local source: { any } = sources[i]
		local observer = source[OBSERVERS]
		if observer then
			table.insert(observer, node)
		else
			source[OBSERVERS] = { node }
		end
	end
end


local function strictWarn(fn: (() -> any)?, err: string)
	local label
	if not fn then
		label = "computation"
	else
		local source, line = debug.info(fn, "sl")
		label = if source then `"{source}:{line}"` else "computation"
	end
	warn(debug.traceback(`[strict] {label} {err}`, 3))
end

local function strictRun(fn: (...any) -> any, oldValue: any, firstResult: any)
	local prevReaction, prevScope, sandbox: { any } = currentReaction, currentScope, {}
	currentReaction, currentScope = nil, sandbox
	local ok, result = xpcall(fn, debug.traceback :: any, oldValue)
	currentReaction, currentScope = prevReaction, prevScope
	dispose(sandbox)
	if not ok then
		strictWarn(fn, `computation threw on rerun: {result}`)
	elseif result ~= firstResult then
		strictWarn(fn, `non-deterministic computation detected`)
	end
end

local function update(node: { any })
	local fn, oldValue = node[FN], node[VALUE]

	doCleanup(node, false)
	disposeChildren(node)

	local prevReaction, prevScope = currentReaction, currentScope
	local prevGets, prevIndex, prevEvalId = currentGets, currentGetsIndex, currentEvalId
	evalId += 1
	currentReaction, currentScope = node, node
	currentGets, currentGetsIndex, currentEvalId = nil, 0, evalId

	local result = fn(oldValue)

	local gets: { any }? = currentGets
	currentReaction, currentScope = prevReaction, prevScope

	if gets then
		updateSources(node, gets)
	else
		local sources = node[SOURCES]
		if sources then
			local index, len = currentGetsIndex, #sources
			if index < len then
				removeSourceObservers(node, index)
				for i = len, index + 1, -1 do
					sources[i] = nil
				end
			end
		end
	end

	currentGets, currentGetsIndex, currentEvalId = prevGets, prevIndex, prevEvalId
	node[STATE], node[VALUE] = CLEAN, result

	if strict_mode then
		strictRun(fn, oldValue, result)
	end

	local observers: { any }? = node[OBSERVERS]
	if observers then
		local eq: ((any, any) -> boolean)? = node[EQUALS]
		if if eq then not eq(oldValue, result) else oldValue ~= result then
			for _, observer in observers do
				observer[STATE] = DIRTY
			end
		end
	end
end

local function updateIfNecessary(node: { any })
	if node[STATE] == CHECK then
		local sources: { any } = node[SOURCES]
		for _, source in sources do
			updateIfNecessary(source)
			if node[STATE] == DIRTY then
				break
			end
		end
	end
	if node[STATE] == DIRTY then
		update(node)
	end
	node[STATE] = CLEAN
end

local function bringCurrent(node: { any })
	if node[STATE] == DIRTY then
		update(node)
	elseif node[STATE] == CHECK then
		updateIfNecessary(node)
	end
end

local function peekUnwrapped(node: { any }): any
	if node[FN] then
		bringCurrent(node)
		return node[VALUE]
	else
		return node[VALUE]
	end
end

local function getData(node: { any }): any
	return peekUnwrapped(track(node))
end

local function setData(node: { any }, value: any)
	local eq: ((any, any) -> boolean)? = node[EQUALS]
	if if eq then not eq(node[VALUE], value) else node[VALUE] ~= value then
		node[VALUE] = value
		forObservers(node, stale)
	end
end

local function peek(node: { any }): any
	return peekUnwrapped(unwrap(node))
end

local function get(node: { any }): any
	return getData(unwrap(node))
end

local function set(node: { any }, value: any)
	setData(unwrap(node), value)
end

local function drain(index: number)
	while index <= effectQueueN do
		drainIndex = index
		local node = effectQueue[index]
		effectQueue[index] = nil
		if node[EFFECT] then
			bringCurrent(node)
		end
		index += 1
	end
	effectQueueN = 0
end

local function resetEval()
	currentReaction, currentScope, currentGets, currentGetsIndex = nil, nil, nil, 0
end

local function flush(index: number?)
	if effectQueueN == 0 then
		return
	end
	local ok, result = (xpcall :: any)(drain, debug.traceback, index or 1)
	if not ok then
		resetEval()
		effectQueue[drainIndex] = nil
		warn(`suppressed effect error:\n{result}`)
		flush(drainIndex + 1)
	end
end

local function adopt(node: { any }): { any }
	local scope = currentScope
	if scope then
		node[SCOPE] = scope
		local children = scope[CHILDREN]
		if children then
			table.insert(children, node)
		else
			scope[CHILDREN] = { node }
		end
	end

	return node
end

local function cleanupFactory(obj: any)
	--stylua: ignore start
	return if typeof(obj) == "RBXScriptConnection" then function() obj:Disconnect() end
		elseif type(obj) == "thread" then function() task.cancel(obj) end
		elseif typeof(obj) == "Instance" then function() obj:Destroy() end
		elseif obj.destroy then function() (obj :: any):destroy() end
		elseif obj.disconnect then function() (obj :: any):disconnect() end
		elseif obj.Destroy then function() (obj :: any):Destroy() end
		elseif obj.Disconnect then function() (obj :: any):Disconnect() end
		else error "cannot cleanup given object"
	--stylua: ignore end
end

type Destroyable = { destroy: (any) -> () } | { Destroy: (any) -> () }
type Disconnectable = { disconnect: (any) -> () } | { Disconnect: (any) -> () }

local cleanup = function(obj: any)
	local scope = currentScope
	if not scope then
		error "cannot cleanup outside a stable or reactive scope"
	end

	if type(obj) ~= "function" then
		obj = cleanupFactory(obj)
	end

	local cleanups = scope[CLEANUPS]
	if cleanups then
		table.insert(cleanups, obj)
	else
		scope[CLEANUPS] = { obj }
	end
end :: ((callback: () -> ()) -> ()) & ((thread: thread) -> ()) & ((instance: Destroyable) -> ()) & ((connection: Disconnectable) -> ()) & ((instance: Instance) -> ()) & ((connection: RBXScriptConnection) -> ())

local function getScope(): { any }?
	return currentScope
end

local function withScope<T>(node: { any }, fn: () -> T): T
	local prev = currentScope
	currentScope = unwrap(node)
	local ok, result = xpcall(fn, debug.traceback :: any)
	currentScope = prev
	if not ok then
		error(`error while running withScope():\n{result}`, 0)
	end
	return result
end

local function untrack<T>(fn: (() -> T)?): T?
	if not fn then
		currentReaction = nil
		return nil
	end
	local previousReaction = currentReaction
	currentReaction = nil
	local ok, result = xpcall(fn, debug.traceback :: any)
	currentReaction = previousReaction
	if not ok then
		error(`error while running untrack():\n{result}`, 0)
	end
	return result
end

local function retrack()
	currentReaction = currentScope
end

local function scope<T>(fn: (destroy: () -> ()) -> T): (() -> (), T)
	local node: { any } = adopt({ false, false, false, false, false, false, false, {}, false, false, false })

	local function remove()
		if not node[CHILDREN] then
			error "root already destroyed"
		end
		dispose(node)
	end

	local prevReaction, prevScope = currentReaction, currentScope
	currentReaction, currentScope = nil, node
	local ok, result = xpcall(fn, debug.traceback :: any, remove)
	currentReaction, currentScope = prevReaction, prevScope
	currentGets, currentGetsIndex = nil, 0

	if not ok then
		remove()
		error(`error while running root():\n{result}`, 0)
	end

	return remove, result
end

local function signal(initial: any, equals: ((any, any) -> boolean)?): { any }
	if equals then
		return setmetatable({ { initial, false, equals } }, Node) :: any
	end
	return setmetatable({ { initial } }, Node) :: any
end

local function computed(fn: () -> any, equals: ((any, any) -> boolean)?): { any }
	return setmetatable({ adopt({ false, false, equals or false, false, DIRTY, fn }) }, Node)
end

local function effect(fn: (...any) -> ()): () -> ()
	local node: { any } = adopt({ false, false, false, false, DIRTY, fn, false, false, false, false, true })

	update(node)

	return function()
		dispose(node)
	end
end

local function new(obj: any, isEffect: boolean?, equals: ((any, any) -> boolean)?): { any } | () -> ()
	if type(obj) == "table" and getmetatable(obj) == Node then
		return obj
	end
	if type(obj) == "function" then
		if isEffect then
			return effect(obj)
		end
		return computed(obj, equals)
	end
	return signal(obj, equals)
end

local function raw(object: any): any
	if isNode(object) then
		return peek(object)
	end
	return object
end

local function read(object: any): any
	if isNode(object) then
		return get(object)
	end
	return object
end

local function destroyPublic(node: { any })
	dispose(unwrap(node))
end

Node = {
	get = function(self: any): any
		return getData(self[1])
	end,
	set = function(self: any, value: any)
		setData(self[1], value)
	end,
	peek = function(self: any): any
		return peekUnwrapped(self[1])
	end,
	Destroy = function(self: any)
		dispose(self[1])
	end,

	--stylua: ignore start
	__call = function(self: any, value: any?, equals: any?): any? return if equals or value ~= nil then setData(self[1], value) else getData(self[1]) end,
	__concat = function(a: any, b: any): string return read(a) .. read(b) end,
	__tostring = function(a: any): string return tostring(getData(a[1])) end,
	__unm = function(a: any): number return -getData(a[1]) end,
	__add = function(a: any, b: any): number return read(a) + read(b) end,
	__sub = function(a: any, b: any): number return read(a) - read(b) end,
	__mul = function(a: any, b: any): number return read(a) * read(b) end,
	__div = function(a: any, b: any): number return read(a) / read(b) end,
	__idiv = function(a: any, b: any): number return read(a) // read(b) end,
	__mod = function(a: any, b: any): number return read(a) % read(b) end,
	__pow = function(a: any, b: any): number return read(a) ^ read(b) end,
	__lt = function(a: any, b: any): boolean return getData(a[1]) < getData(b[1]) end,
	__le = function(a: any, b: any): boolean return getData(a[1]) <= getData(b[1]) end,
	--stylua: ignore end
}

return setmetatable({
	strict = strict,
	new = new,
	signal = signal,
	computed = computed,
	effect = effect,
	flush = flush,
	scope = scope,
	cleanup = cleanup,
	getScope = getScope,
	withScope = withScope,
	untrack = untrack,
	retrack = retrack,
	get = get,
	set = set,
	peek = peek,
	raw = raw,
	read = read,
	isNode = isNode,
	isReactive = isReactive,
	destroy = destroyPublic,
	Handle = Node,
}, { __call = new })
