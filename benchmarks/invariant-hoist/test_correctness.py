from workload import rank_queries


assert rank_queries([3, 1, 2], [10, 20]) == [(10, 1, 3), (20, 1, 3)]
assert rank_queries([5], [1]) == [(1, 5, 5)]
