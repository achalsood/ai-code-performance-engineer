from solution import encode
values = list(range(30000))
for _ in range(8):
    result = encode(values)
assert result.startswith("0|1|2|")
