from __future__ import annotations

import ast
from pathlib import Path

from .models import Finding


class PerformanceVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.findings: list[Finding] = []
        self._loop_depth = 0

    def visit_For(self, node: ast.For) -> None:
        self._loop_depth += 1
        if self._loop_depth >= 2:
            self._add(
                "PERF001",
                node,
                "medium",
                "Nested loop may scale quadratically.",
                "Consider indexing lookup data in a set or dictionary.",
            )
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._loop_depth += 1
        if self._loop_depth >= 2:
            self._add(
                "PERF001",
                node,
                "medium",
                "Nested loop may scale quadratically.",
                "Consider indexing lookup data in a set or dictionary.",
            )
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        function_name = node.func.id if isinstance(node.func, ast.Name) else None
        if self._loop_depth and function_name in {"sorted", "list"}:
            self._add(
                "PERF002",
                node,
                "medium",
                f"{function_name}() allocates inside a loop.",
                "Hoist invariant allocation outside the loop when semantics permit.",
            )
        if (
            self._loop_depth
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"count", "index"}
        ):
            self._add(
                "PERF003",
                node,
                "high",
                f".{node.func.attr}() performs a linear scan inside a loop.",
                "Precompute a lookup dictionary or set.",
            )
        self.generic_visit(node)

    def _add(
        self, rule_id: str, node: ast.AST, severity: str, message: str, suggestion: str
    ) -> None:
        self.findings.append(
            Finding(
                rule_id,
                str(self.path),
                getattr(node, "lineno", 0),
                severity,
                message,
                suggestion,
            )
        )


def analyze_file(path: Path) -> list[Finding]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return []
    visitor = PerformanceVisitor(path)
    visitor.visit(tree)
    return visitor.findings


def analyze_path(path: Path) -> list[Finding]:
    files = [path] if path.is_file() else sorted(path.rglob("*.py"))
    return [finding for file in files for finding in analyze_file(file)]
