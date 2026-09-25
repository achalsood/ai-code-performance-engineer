from collections import Counter

def frequencies(values, queries):
    counts = Counter(values)
    return [counts[query] for query in queries]
