import json
from pathlib import Path

from perf_engineer.audit import AuditLogger


def test_detects_audit_log_tampering(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path)
    logger.append("started", {"revision": "abc"})
    logger.append("finished", {"winner": "one"})
    assert logger.verify()

    records = path.read_text().splitlines()
    first = json.loads(records[0])
    first["data"]["revision"] = "tampered"
    records[0] = json.dumps(first)
    path.write_text("\n".join(records) + "\n")
    assert not logger.verify()
