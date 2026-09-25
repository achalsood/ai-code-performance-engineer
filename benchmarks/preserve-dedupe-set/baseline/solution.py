def unique(values):
    out = []
    for value in values:
        if value not in out:
            out.append(value)
    return out
