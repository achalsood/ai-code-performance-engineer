def prefix_sums(values):
    out = []
    total = 0
    for value in values:
        total += value
        out.append(total)
    return out
