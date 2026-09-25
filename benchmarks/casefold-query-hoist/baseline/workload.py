from solution import contains_query
values = [f"Record-{i}-Performance-Engineering" for i in range(70000)]
query = "PERFORMANCE"
for _ in range(12):
    result = contains_query(values, query)
assert len(result) == len(values)
