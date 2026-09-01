values = list(range(30_000, 0, -1))
checksum = 0
for offset in range(500):
    checksum += sorted(values)[offset]
assert checksum == 125_250
