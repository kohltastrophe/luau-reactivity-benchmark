-- Port of the cellx benchmark from milomg/js-reactivity-benchmark (cellxBench.ts).

local harness = require("../harness")

-- Builds the lattice and returns upstream's timed iterator: read the last layer, write the four sources in
-- one batch, read it again, and return both readings.
local function build(bridge, layers: number)
	return bridge.withBuild(function()
		local start = {
			prop1 = bridge.signal(1),
			prop2 = bridge.signal(2),
			prop3 = bridge.signal(3),
			prop4 = bridge.signal(4),
		}
		local layer = start
		for _ = layers, 1, -1 do
			local m = layer
			local s = {
				prop1 = bridge.computed(function()
					return m.prop2.read()
				end),
				prop2 = bridge.computed(function()
					return m.prop1.read() - m.prop3.read()
				end),
				prop3 = bridge.computed(function()
					return m.prop2.read() + m.prop4.read()
				end),
				prop4 = bridge.computed(function()
					return m.prop3.read()
				end),
			}
			bridge.effect(function()
				s.prop1.read()
			end)
			bridge.effect(function()
				s.prop2.read()
			end)
			bridge.effect(function()
				s.prop3.read()
			end)
			bridge.effect(function()
				s.prop4.read()
			end)
			layer = s
		end
		local e = layer
		return function(): ({ number }, { number })
			local before = { e.prop1.read(), e.prop2.read(), e.prop3.read(), e.prop4.read() }
			bridge.withBatch(function()
				start.prop1.write(4)
				start.prop2.write(3)
				start.prop3.write(2)
				start.prop4.write(1)
			end)
			local after = { e.prop1.read(), e.prop2.read(), e.prop3.read(), e.prop4.read() }
			return before, after
		end
	end)
end

-- Upstream's golden leaf values, the same at both sizes.
local function expect(before: { number }, after: { number })
	local got = table.concat(before, ",") .. " / " .. table.concat(after, ",")
	local want = "-3,-6,-2,2 / -2,-4,2,3"
	if got ~= want then
		harness.fail("wrong", `wrong value: \{{got}}, expected \{{want}}`)
	end
end

local M = { cases = {}, checks = {} }

for _, layers in { 1000, 2500 } do
	local name = "cellx" .. layers
	table.insert(M.cases, {
		name = name,
		-- upstream times each run from a fresh build and reports their total; this reports the mean
		run = function(bridge)
			build(bridge, layers)() -- upstream's warm-up run
			bridge.cleanup()
			return harness.mean(10, function()
				local propagate = build(bridge, layers)
				harness.gc()
				local t0 = os.clock()
				local before, after = propagate()
				local secs = os.clock() - t0
				bridge.cleanup()
				expect(before, after) -- upstream asserts the timed runs' values too
				return secs
			end)
		end,
		-- one propagation, as upstream's warm-up run
		evals = function(bridge)
			local computed, effect = harness.count(bridge, function(counted, reset)
				local propagate = build(counted, layers)
				reset()
				propagate()
			end)
			return computed + effect
		end,
	})
	table.insert(M.checks, {
		name = name,
		run = function(bridge)
			local before, after = build(bridge, layers)()
			bridge.cleanup()
			expect(before, after)
		end,
	})
end

return M
