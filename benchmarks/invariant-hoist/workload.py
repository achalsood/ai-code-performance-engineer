def rank_queries(values, queries):
    result = []
    for query in queries:
        ordered = sorted(values)
        result.append((query, ordered[0], ordered[-1]))
    return result


values = list(range(6000, 0, -1))
queries = list(range(250))
rank_queries(values, queries)
