import json

def score(records, config_text):
    config = json.loads(config_text)
    multiplier = config["multiplier"]
    return sum(record["value"] * multiplier for record in records)
