import re

def count_matches(values):
    count = 0
    for value in values:
        if re.compile(r"^[a-z]+-[0-9]{4}$").match(value):
            count += 1
    return count
