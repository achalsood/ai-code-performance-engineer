from __future__ import annotations

import ast
import os
from pathlib import Path

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from .models import Finding

SUPPORTED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
EXCLUDED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "vendor",
    "__pycache__",
}


def _bound_names(target: ast.expr) -> set[str]:
    return {node.id for node in ast.walk(target) if isinstance(node, ast.Name)}


def _scope_set_names(scope: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and (
            isinstance(node.value, (ast.Set, ast.SetComp))
            or (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "set"
            )
        ):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
    return names


class PerformanceVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, set_names: set[str]) -> None:
        self.path = path
        self.findings: list[Finding] = []
        self._loop_depth = 0
        self._loop_bound_names: list[set[str]] = []
        self._set_names = set_names

    def visit_For(self, node: ast.For) -> None:
        self._loop_depth += 1
        self._loop_bound_names.append(_bound_names(node.target))
        if self._loop_depth >= 2:
            self._add(
                "PERF001",
                node,
                "medium",
                "Nested loop may scale quadratically.",
                "Consider indexing lookup data in a set or dictionary.",
            )
        self.generic_visit(node)
        self._loop_bound_names.pop()
        self._loop_depth -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._loop_depth += 1
        self._loop_bound_names.append(_bound_names(node.target))
        if self._loop_depth >= 2:
            self._add(
                "PERF001",
                node,
                "medium",
                "Nested loop may scale quadratically.",
                "Consider indexing lookup data in a set or dictionary.",
            )
        self.generic_visit(node)
        self._loop_bound_names.pop()
        self._loop_depth -= 1

    def visit_Compare(self, node: ast.Compare) -> None:
        if (
            self._loop_depth
            and len(node.ops) == 1
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id in set().union(*self._loop_bound_names)
            and node.comparators[0].id not in self._set_names
        ):
            self._add(
                "PERF004",
                node,
                "high",
                "Linear membership lookup executes inside a loop.",
                "Precompute a set outside the loop when hash semantics permit.",
            )
        self.generic_visit(node)

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
    visitor = PerformanceVisitor(path, _scope_set_names(tree))
    visitor.visit(tree)
    return visitor.findings


def analyze_path(path: Path) -> list[Finding]:
    files = [path] if path.is_file() else discover_files(path)
    findings: list[Finding] = []
    for file in files:
        if file.suffix == ".py":
            findings.extend(analyze_file(file))
        else:
            findings.extend(analyze_javascript_file(file))
    return findings


def discover_files(path: Path) -> list[Path]:
    """Discover supported files without descending into dependency or build trees."""
    files: list[Path] = []
    for root, directories, filenames in os.walk(path):
        directories[:] = sorted(
            directory for directory in directories if directory not in EXCLUDED_DIRECTORIES
        )
        directory_path = Path(root)
        files.extend(
            directory_path / filename
            for filename in sorted(filenames)
            if Path(filename).suffix in SUPPORTED_SUFFIXES
        )
    return files


_LOOP_NODES = {"for_statement", "for_in_statement", "while_statement", "do_statement"}


def _javascript_parser(path: Path) -> Parser:
    if path.suffix in {".ts", ".tsx"}:
        language = (
            tree_sitter_typescript.language_tsx()
            if path.suffix == ".tsx"
            else tree_sitter_typescript.language_typescript()
        )
    else:
        language = tree_sitter_javascript.language()
    return Parser(Language(language))


def analyze_javascript_file(path: Path) -> list[Finding]:
    try:
        source = path.read_bytes()
        tree = _javascript_parser(path).parse(source)
    except (OSError, ValueError):
        return []
    findings: list[Finding] = []

    def add(rule: str, node: Node, severity: str, message: str, suggestion: str) -> None:
        findings.append(
            Finding(rule, str(path), node.start_point.row + 1, severity, message, suggestion)
        )

    def visit(node: Node, loop_depth: int) -> None:
        is_loop = node.type in _LOOP_NODES
        next_depth = loop_depth + int(is_loop)
        if is_loop and next_depth >= 2:
            add(
                "PERF101",
                node,
                "medium",
                "Nested JavaScript loop may scale quadratically.",
                "Consider indexing lookup data in a Set or Map.",
            )
        if node.type == "call_expression" and loop_depth:
            call_text = source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
            if any(f".{method}(" in call_text for method in ("includes", "indexOf")):
                add(
                    "PERF102",
                    node,
                    "high",
                    "Linear array lookup executes inside a loop.",
                    "Precompute a Set or Map outside the loop.",
                )
            if ".sort(" in call_text or call_text.startswith("Array.from("):
                add(
                    "PERF103",
                    node,
                    "medium",
                    "Allocation or sorting executes inside a loop.",
                    "Hoist invariant work outside the loop when semantics permit.",
                )
        for child in node.children:
            visit(child, next_depth)

    visit(tree.root_node, 0)
    return findings
