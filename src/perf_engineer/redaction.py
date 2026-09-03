from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    content: str
    redaction_count: int


PATTERNS = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b"),
    re.compile(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\b"
        r"\s*[:=]\s*)(['\"])([^'\"\n]{6,})(\2)"
    ),
)


def redact_secrets(content: str) -> RedactionResult:
    count = 0

    def replace_entire(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[REDACTED]"

    redacted = PATTERNS[0].sub(replace_entire, content)

    def replace_assignment(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}"

    redacted = PATTERNS[2].sub(replace_assignment, redacted)
    redacted = PATTERNS[1].sub(replace_entire, redacted)
    return RedactionResult(redacted, count)
