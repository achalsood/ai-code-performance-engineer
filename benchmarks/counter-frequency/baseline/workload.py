from solution import frequencies
values = [i % 5000 for i in range(50000)]
queries = list(range(5000))
for _ in range(2):
    result = frequencies(values, queries)
assert sum(result) == len(values)
