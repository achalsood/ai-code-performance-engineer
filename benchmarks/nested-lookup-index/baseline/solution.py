def resolve(ids, records):
    out = []
    for item_id in ids:
        for record in records:
            if record["id"] == item_id:
                out.append(record["value"])
                break
    return out
