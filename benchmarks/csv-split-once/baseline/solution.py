def totals(rows):
    total = 0
    for row in rows:
        total += int(row.split(",")[1]) * int(row.split(",")[2])
    return total
