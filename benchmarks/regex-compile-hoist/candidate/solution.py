import re

_PATTERN = re.compile(r"^[a-z]+-[0-9]{4}$")

def count_matches(values):
    return sum(1 for value in values if _PATTERN.match(value))
