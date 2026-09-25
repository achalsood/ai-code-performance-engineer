from solution import totals
rows = [f"item{i},{i % 9 + 1},{i % 101 + 1}" for i in range(60000)]
for _ in range(8):
    result = totals(rows)
assert result > 0
