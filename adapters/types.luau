--!strict

--[[
The adapter interface, milomg/js-reactivity-benchmark's `ReactiveFramework`:

	signal(initial) -> { read, write }   a writable source
	computed(fn)    -> { read }          a memoized derivation; fn reads via node.read()
	effect(fn)                           a reaction; re-runs when its reads change
	withBatch(fn)                        run fn, then settle the graph
	withBuild(fn)   -> any               run fn inside an owner/root, returns its result
	cleanup()                            dispose everything the last withBuild created (idempotent)

Every withBuild is followed by cleanup() before the next one.
]]

export type Node = {
	read: () -> any,
	write: ((any) -> ())?,
}

export type Framework = {
	signal: (initial: any) -> Node,
	computed: (fn: () -> any) -> Node,
	effect: (fn: () -> ()) -> (),
	withBatch: (fn: () -> ()) -> (),
	withBuild: <T>(fn: () -> T) -> T,
	cleanup: () -> (),
}

return {}
