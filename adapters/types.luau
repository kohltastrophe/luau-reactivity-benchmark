--!strict

--[[
Shared adapter interface, mirroring the `ReactiveFramework` contract from milomg/js-reactivity-benchmark

	signal(initial) -> { read, write }   a writable source
	computed(fn)    -> { read }          a memoized derivation; fn reads via node.read()
	effect(fn)                           a reaction; re-runs when its reads change
	withBatch(fn)                        run fn, then settle the graph
	withBuild(fn)   -> any               run fn inside an owner/root, returns its result
	cleanup()                            dispose everything created by the last withBuild

A framework's display label is its adapter folder name (supplied by benchmark.py)

`DNF` pre-declares specific benches the framework can't finish, mapping each bench key
(e.g. "cellx/cellx1000", "dynamic/25-1000x5") to a short reason shown on the chart
]]

export type Node = {
	read: () -> any,
	write: ((any) -> ())?,
}

export type Framework = {
	DNF: { [string]: string }?,
	signal: (initial: any) -> Node,
	computed: (fn: () -> any) -> Node,
	effect: (fn: () -> ()) -> (),
	withBatch: (fn: () -> ()) -> (),
	withBuild: <T>(fn: () -> T) -> T,
	cleanup: () -> (),
}

return {}
