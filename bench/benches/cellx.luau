-- Port of the cellx benchmark from milomg/js-reactivity-benchmark (cellxBench.ts).

local M = {}

function M.build(bridge, layers: number)
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

return M
