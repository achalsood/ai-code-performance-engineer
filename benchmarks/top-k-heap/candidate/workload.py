from solution import top_k
values = [(i * 48271) % 2147483647 for i in range(120000)]
for _ in range(6):
    result = top_k(values, 20)
assert len(result) == 20
