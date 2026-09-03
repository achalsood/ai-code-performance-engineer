from __future__ import annotations

from typing import Any

from .models import Finding


def findings_to_sarif(findings: list[Finding]) -> dict[str, Any]:
    rule_map = {finding.rule_id: finding for finding in findings}
    rules = [
        {
            "id": rule_id,
            "name": rule_id,
            "shortDescription": {"text": example.message},
            "help": {"text": example.suggestion},
            "defaultConfiguration": {
                "level": {"high": "error", "medium": "warning", "low": "note"}.get(
                    example.severity, "warning"
                )
            },
        }
        for rule_id, example in sorted(rule_map.items())
    ]
    results = [
        {
            "ruleId": finding.rule_id,
            "level": {"high": "error", "medium": "warning", "low": "note"}.get(
                finding.severity, "warning"
            ),
            "message": {"text": f"{finding.message} {finding.suggestion}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": finding.path},
                        "region": {"startLine": finding.line},
                    }
                }
            ],
        }
        for finding in findings
    ]
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "AI Code Performance Engineer",
                        "semanticVersion": "1.0.0",
                        "informationUri": (
                            "https://github.com/achalsood/ai-code-performance-engineer"
                        ),
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
