def resolve(ids, records):
    index = {record["id"]: record["value"] for record in records}
    return [index[item_id] for item_id in ids if item_id in index]
