from solution import pack
assert pack([b"ab", b"", b"cd"]) == b"abcd"
assert pack([]) == b""
