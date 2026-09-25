from solution import score
import json
records = [{"value": i % 17} for i in range(30000)]
config = json.dumps({"multiplier": 7, "name": "benchmark", "flags": list(range(20))})
for _ in range(8):
    result = score(records, config)
assert result > 0
