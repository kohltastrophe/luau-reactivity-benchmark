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

export type Computed = {
	read: () -> any,
}

export type Signal = {
	read: () -> any,
	write: (any) -> ...any,
}

export type Framework = {
	signal: (initial: any) -> Signal,
	computed: (fn: () -> any) -> Computed,
	effect: (fn: () -> ...any) -> (),
	withBatch: (fn: () -> ()) -> (),
	withBuild: <T>(fn: () -> T) -> T,
	cleanup: () -> (),
}

export type Case = {
	name: string,
	per: number?,
	run: (bridge: Framework) -> number,
	evals: ((bridge: Framework) -> number)?,
}

export type Check = {
	name: string,
	scale: boolean?,
	run: (bridge: Framework) -> (),
}

export type Suite = {
	cases: { Case },
	checks: { Check },
}

return {}
