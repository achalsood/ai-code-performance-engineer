from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLogger:
    """Append-only hash chain; changes to earlier records invalidate every later link."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: str, data: dict[str, Any]) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            previous_hash = self._last_hash(stream)
            body = {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                "data": data,
                "previous_hash": previous_hash,
            }
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            stream.seek(0, os.SEEK_END)
            stream.write(json.dumps({**body, "hash": digest}, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            return digest

    @staticmethod
    def _last_hash(stream: Any) -> str:
        stream.seek(0, os.SEEK_END)
        end = stream.tell()
        if end == 0:
            return "0" * 64
        position = end - 1
        while position > 0:
            stream.seek(position)
            if stream.read(1) == "\n" and position < end - 1:
                break
            position -= 1
        stream.seek(position + 1 if position else 0)
        line = stream.readline().strip()
        digest = json.loads(line)["hash"]
        if not isinstance(digest, str):
            raise ValueError("invalid audit hash")
        return digest

    def verify(self) -> bool:
        previous_hash = "0" * 64
        if not self.path.exists():
            return True
        for line in self.path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            digest = record.pop("hash")
            if record["previous_hash"] != previous_hash:
                return False
            expected = hashlib.sha256(
                json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if digest != expected:
                return False
            previous_hash = digest
        return True
