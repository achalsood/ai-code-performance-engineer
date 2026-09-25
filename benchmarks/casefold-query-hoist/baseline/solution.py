def contains_query(values, query):
    return [value for value in values if query.casefold() in value.casefold()]
