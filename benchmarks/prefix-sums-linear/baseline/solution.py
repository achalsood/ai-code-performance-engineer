def prefix_sums(values):
    return [sum(values[: index + 1]) for index in range(len(values))]
