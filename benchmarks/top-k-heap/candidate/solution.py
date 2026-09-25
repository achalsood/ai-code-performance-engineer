import heapq

def top_k(values, k):
    return sorted(heapq.nlargest(k, values), reverse=True)
