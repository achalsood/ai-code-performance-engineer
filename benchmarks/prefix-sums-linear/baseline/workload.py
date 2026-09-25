from solution import prefix_sums
values = list(range(6000))
for _ in range(5):
    result = prefix_sums(values)
assert result[-1] == sum(values)
