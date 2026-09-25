from solution import frequencies
values = [i % 5000 for i in range(100000)]
queries = list(range(5000))
for _ in range(3):
    result = frequencies(values, queries)
assert sum(result) == len(values)
