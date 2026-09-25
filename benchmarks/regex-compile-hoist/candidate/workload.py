from solution import count_matches
values = [f"item-{i % 10000:04d}" for i in range(40000)]
for _ in range(12):
    result = count_matches(values)
assert result == 40000
