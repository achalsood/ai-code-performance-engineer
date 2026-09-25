def pack(chunks):
    output = bytearray()
    for chunk in chunks:
        output.extend(chunk)
    return bytes(output)
