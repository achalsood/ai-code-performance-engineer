items = list(range(4_000))
queries = list(range(2_000, 6_000))
index = set(items)
result = sum(query in index for query in queries)
assert result == 2_000
