def select_allowed(values, allowed):
    allowed_index = set(allowed)
    return [value for value in values if value in allowed_index]
