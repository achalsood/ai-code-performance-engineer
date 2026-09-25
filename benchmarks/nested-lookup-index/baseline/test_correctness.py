from solution import resolve
records = [{"id": 1, "value": "a"}, {"id": 2, "value": "b"}]
assert resolve([2, 1], records) == ["b", "a"]
assert resolve([], records) == []
