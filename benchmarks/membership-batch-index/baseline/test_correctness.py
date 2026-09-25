from solution import select_allowed
assert select_allowed([4, 1, 3, 2], [1, 2, 4]) == [4, 1, 2]
assert select_allowed([], [1]) == []
