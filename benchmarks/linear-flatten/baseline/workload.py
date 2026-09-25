from solution import flatten
groups = [list(range(30)) for _ in range(2500)]
for _ in range(4):
    result = flatten(groups)
assert len(result) == 75000
