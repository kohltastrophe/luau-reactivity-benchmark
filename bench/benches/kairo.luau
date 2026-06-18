-- Port of the kairo suite from milomg/js-reactivity-benchmark (packages/core/src/benches/kairo/*).

local function busy()
	local a = 0
	for _ = 1, 100 do
		a += 1
	end
	return a
end

local function avoidablePropagation(bridge): (boolean?) -> ()
	local head = bridge.signal(0)
	local c1 = bridge.computed(function()
		return head.read()
	end)
	local c2 = bridge.computed(function()
		c1.read()
		return 0
	end)
	local c3 = bridge.computed(function()
		busy()
		return c2.read() + 1
	end)
	local c4 = bridge.computed(function()
		return c3.read() + 2
	end)
	local c5 = bridge.computed(function()
		return c4.read() + 3
	end)
	bridge.effect(function()
		c5.read()
		busy()
	end)
	return function(verify: boolean?)
		bridge.withBatch(function()
			head.write(1)
		end)
		if verify then
			assert(c5.read() == 6, "avoidable c5 baseline")
		end
		for i = 0, 999 do
			bridge.withBatch(function()
				head.write(i)
			end)
			local v = c5.read()
			if verify then
				assert(v == 6, "avoidable c5")
			end
		end
	end
end

local function broadPropagation(bridge): (boolean?) -> ()
	local head = bridge.signal(0)
	local last
	for i = 0, 49 do
		local current = bridge.computed(function()
			return head.read() + i
		end)
		local current2 = bridge.computed(function()
			return current.read() + 1
		end)
		bridge.effect(function()
			current2.read()
		end)
		last = current2
	end
	return function(verify: boolean?)
		bridge.withBatch(function()
			head.write(1)
		end)
		for i = 0, 49 do
			bridge.withBatch(function()
				head.write(i)
			end)
			local v = last.read()
			if verify then
				assert(v == i + 50, "broad last")
			end
		end
	end
end

local function deepPropagation(bridge): (boolean?) -> ()
	local len = 50
	local head = bridge.signal(0)
	local current = head
	for _ = 1, len do
		local c = current
		current = bridge.computed(function()
			return c.read() + 1
		end)
	end
	bridge.effect(function()
		current.read()
	end)
	return function(verify: boolean?)
		bridge.withBatch(function()
			head.write(1)
		end)
		for i = 0, 49 do
			bridge.withBatch(function()
				head.write(i)
			end)
			local v = current.read()
			if verify then
				assert(v == len + i, "deep leaf")
			end
		end
	end
end

local function diamond(bridge): (boolean?) -> ()
	local width = 5
	local head = bridge.signal(0)
	local current = {}
	for _ = 1, width do
		table.insert(
			current,
			bridge.computed(function()
				return head.read() + 1
			end)
		)
	end
	local sum = bridge.computed(function()
		local s = 0
		for _, x in current do
			s += x.read()
		end
		return s
	end)
	bridge.effect(function()
		sum.read()
	end)
	return function(verify: boolean?)
		bridge.withBatch(function()
			head.write(1)
		end)
		if verify then
			assert(sum.read() == 2 * width, "diamond baseline")
		end
		for i = 0, 499 do
			bridge.withBatch(function()
				head.write(i)
			end)
			local v = sum.read()
			if verify then
				assert(v == (i + 1) * width, "diamond sum")
			end
		end
	end
end

local function mux(bridge): (boolean?) -> ()
	local heads = {}
	for k = 0, 99 do
		heads[k] = bridge.signal(0)
	end
	local muxc = bridge.computed(function()
		local t = {}
		for k = 0, 99 do
			t[k] = heads[k].read()
		end
		return t
	end)
	local split = {}
	for k = 0, 99 do
		local k0 = k
		local s1 = bridge.computed(function()
			return muxc.read()[k0]
		end)
		split[k] = bridge.computed(function()
			return s1.read() + 1
		end)
	end
	for k = 0, 99 do
		local k0 = k
		bridge.effect(function()
			split[k0].read()
		end)
	end
	return function(verify: boolean?)
		for i = 0, 9 do
			bridge.withBatch(function()
				heads[i].write(i)
			end)
			local v = split[i].read()
			if verify then
				assert(v == i + 1, "mux a")
			end
		end
		for i = 0, 9 do
			bridge.withBatch(function()
				heads[i].write(i * 2)
			end)
			local v = split[i].read()
			if verify then
				assert(v == i * 2 + 1, "mux b")
			end
		end
	end
end

local function repeatedObservers(bridge): (boolean?) -> ()
	local size = 30
	local head = bridge.signal(0)
	local current = bridge.computed(function()
		local result = 0
		for _ = 1, size do
			result += head.read()
		end
		return result
	end)
	bridge.effect(function()
		current.read()
	end)
	return function(verify: boolean?)
		bridge.withBatch(function()
			head.write(1)
		end)
		if verify then
			assert(current.read() == size, "repeated baseline")
		end
		for i = 0, 99 do
			bridge.withBatch(function()
				head.write(i)
			end)
			local v = current.read()
			if verify then
				assert(v == i * size, "repeated")
			end
		end
	end
end

local function triangle(bridge): (boolean?) -> ()
	local width = 10
	local head = bridge.signal(0)
	local current = head
	local list = {}
	for _ = 1, width do
		local c = current
		table.insert(list, current)
		current = bridge.computed(function()
			return c.read() + 1
		end)
	end
	local sum = bridge.computed(function()
		local s = 0
		for _, x in list do
			s += x.read()
		end
		return s
	end)
	bridge.effect(function()
		sum.read()
	end)
	local constant = width * (width + 1) // 2
	return function(verify: boolean?)
		bridge.withBatch(function()
			head.write(1)
		end)
		if verify then
			assert(sum.read() == constant, "triangle baseline")
		end
		for i = 0, 99 do
			bridge.withBatch(function()
				head.write(i)
			end)
			local v = sum.read()
			if verify then
				assert(v == constant - width + i * width, "triangle sum")
			end
		end
	end
end

local function unstable(bridge): (boolean?) -> ()
	local head = bridge.signal(0)
	local double = bridge.computed(function()
		return head.read() * 2
	end)
	local inverse = bridge.computed(function()
		return -head.read()
	end)
	local current = bridge.computed(function()
		local result = 0
		for _ = 1, 20 do
			if head.read() % 2 ~= 0 then
				result += double.read()
			else
				result += inverse.read()
			end
		end
		return result
	end)
	bridge.effect(function()
		current.read()
	end)
	return function(verify: boolean?)
		bridge.withBatch(function()
			head.write(1)
		end)
		if verify then
			assert(current.read() == 40, "unstable baseline")
		end
		for i = 0, 99 do
			bridge.withBatch(function()
				head.write(i)
			end)
			local _ = current.read()
		end
	end
end

-- mol (benches/kairo/molBench.ts, originally the $mol benchmark). Unlike the others this isn't
-- about graph shape: several nodes do real compute via `hard`, which adds fib(16) = 1597 of
-- throwaway work per call, so it measures recompute cost under load. a/b are signals; c-g are
-- computeds; the three effects are upstream's H/I/J.
local function fib(n: number): number
	if n < 2 then
		return 1
	end
	return fib(n - 1) + fib(n - 2)
end

local function hard(n: number): number
	return n + fib(16)
end

local molNumbers = { 0, 1, 2, 3, 4 }

local function mol(bridge): (boolean?) -> ()
	local res = {}
	local a = bridge.signal(0)
	local b = bridge.signal(0)
	local c = bridge.computed(function()
		return (a.read() % 2) + (b.read() % 2)
	end)
	local d = bridge.computed(function()
		local pa, pb = a.read() % 2, b.read() % 2
		local out = {}
		for i = 1, #molNumbers do
			out[i] = { x = molNumbers[i] + pa - pb }
		end
		return out
	end)
	local e = bridge.computed(function()
		return hard(c.read() + a.read() + d.read()[1].x)
	end)
	local f = bridge.computed(function()
		local d3 = d.read()[3].x
		return hard(if d3 ~= 0 then d3 else b.read())
	end)
	local g = bridge.computed(function()
		local cv = c.read()
		return cv + (if cv ~= 0 then cv else e.read() % 2) + d.read()[5].x + f.read()
	end)
	bridge.effect(function()
		table.insert(res, hard(g.read()))
	end)
	bridge.effect(function()
		table.insert(res, g.read())
	end)
	bridge.effect(function()
		table.insert(res, hard(f.read()))
	end)
	local i = 0
	return function(verify: boolean?)
		i += 1
		table.clear(res)
		bridge.withBatch(function()
			b.write(1)
			a.write(1 + i * 2)
		end)
		bridge.withBatch(function()
			a.write(2 + i * 2)
			b.write(2)
		end)
		if verify then
			assert(f.read() == 1599, "mol f")
			assert(g.read() == 1604, "mol g")
		end
	end
end

return {
	{ name = "avoidable", fn = avoidablePropagation },
	{ name = "broad", fn = broadPropagation },
	{ name = "deep", fn = deepPropagation },
	{ name = "diamond", fn = diamond },
	{ name = "mux", fn = mux },
	{ name = "repeated", fn = repeatedObservers },
	{ name = "triangle", fn = triangle },
	{ name = "unstable", fn = unstable },
	{ name = "mol", fn = mol },
}
