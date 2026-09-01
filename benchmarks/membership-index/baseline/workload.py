items = list(range(4_000))
queries = list(range(2_000, 6_000))
result = sum(query in items for query in queries)
assert result == 2_000
