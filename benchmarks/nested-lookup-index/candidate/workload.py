from solution import resolve
records = [{"id": i, "value": i * 3} for i in range(4000)]
ids = list(range(3999, -1, -1))
for _ in range(3):
    result = resolve(ids, records)
assert len(result) == 4000
