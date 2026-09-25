from solution import select_allowed
allowed = list(range(0, 20000, 2))
values = list(range(20000))
for _ in range(8):
    result = select_allowed(values, allowed)
assert len(result) == 10000
