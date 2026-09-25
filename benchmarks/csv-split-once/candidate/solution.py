def totals(rows):
    total = 0
    for row in rows:
        _, quantity, price = row.split(",")
        total += int(quantity) * int(price)
    return total
