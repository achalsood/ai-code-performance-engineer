def pack(chunks):
    output = b""
    for chunk in chunks:
        output += chunk
    return output
