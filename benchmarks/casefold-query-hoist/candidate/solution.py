def contains_query(values, query):
    normalized_query = query.casefold()
    return [value for value in values if normalized_query in value.casefold()]
