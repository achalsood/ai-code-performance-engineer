from solution import unique
values = list(range(5000)) + list(range(5000))
for _ in range(4):
    result = unique(values)
assert len(result) == 5000
