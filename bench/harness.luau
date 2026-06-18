-- Shared timing/RNG helpers for the benchmark (fastest-of-N itself lives in suite.luau).

local M = {}

-- Collect twice to settle finalizers, matching the reference. Best-effort.
function M.gc()
	pcall(function()
		collectgarbage("collect")
		collectgarbage("collect")
	end)
end

-- Deterministic xorshift RNG -> floats in [0, 1). Same seed => same graph for every framework.
function M.rng(seed: number?): () -> number
	local s = (seed or 0x9e3779b9) % 0x100000000
	if s == 0 then
		s = 0x1a2b3c4d
	end
	return function(): number
		s = bit32.bxor(s, bit32.lshift(s, 13))
		s = bit32.bxor(s, bit32.rshift(s, 17))
		s = bit32.bxor(s, bit32.lshift(s, 5))
		return s / 0x100000000
	end
end

return M
