from solution import bounds
values = list(range(80000, 0, -1))
for _ in range(15):
    result = bounds(values)
assert result == (1, 80000)
