values = list(range(30_000, 0, -1))
ordered = sorted(values)
checksum = 0
for offset in range(500):
    checksum += ordered[offset]
assert checksum == 125_250
