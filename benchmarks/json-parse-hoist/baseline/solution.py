import json

def score(records, config_text):
    total = 0
    for record in records:
        config = json.loads(config_text)
        total += record["value"] * config["multiplier"]
    return total
