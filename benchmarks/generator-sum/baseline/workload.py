from solution import squared_total
values = list(range(120000))
for _ in range(25):
    result = squared_total(values)
assert result > 0
