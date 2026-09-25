from solution import pack
chunks = [b"abcdefgh" * 8 for _ in range(12000)]
for _ in range(6):
    result = pack(chunks)
assert len(result) == len(chunks) * 64
