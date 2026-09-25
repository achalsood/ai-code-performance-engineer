from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass

from .providers import OptimizationCandidate, OptimizationRequest


@dataclass(frozen=True)
class _Rewrite:
    title: str
    rationale: str
    strategy: str
    source: str


class DeterministicFixProvider:
    """Generate conservative source rewrites for analyzer findings without an LLM."""

    def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
        candidates: list[OptimizationCandidate] = []
        finding_paths = {finding.path for finding in request.findings}
        for path in sorted(finding_paths):
            source = request.files.get(path)
            if source is None or not path.endswith(".py"):
                continue
            rewrite = _rewrite_python(source)
            if rewrite is None or rewrite.source == source:
                continue
            patch = "".join(
                difflib.unified_diff(
                    source.splitlines(keepends=True),
                    rewrite.source.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
            )
            patch = f"diff --git a/{path} b/{path}\n" + patch
            candidates.append(
                OptimizationCandidate(
                    candidate_id=f"deterministic-{len(candidates) + 1}",
                    title=rewrite.title,
                    rationale=rewrite.rationale,
                    patch=patch,
                    strategy=rewrite.strategy,
                    expected_impact="Remove repeated linear work from a hot loop.",
                    risk="medium; verification remains mandatory",
                )
            )
            if len(candidates) >= request.maximum_candidates:
                break
        return candidates


def _rewrite_python(source: str) -> _Rewrite | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    transformer = _LinearCountTransformer()
    rewritten = transformer.visit(tree)
    if not transformer.changed:
        return None
    ast.fix_missing_locations(rewritten)
    return _Rewrite(
        "Precompute repeated counts",
        "Replaces repeated list.count calls in a loop with one frequency table.",
        "data-structure-index",
        ast.unparse(rewritten) + "\n",
    )


class _LinearCountTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.changed = False
        self.counter_index = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        new_body: list[ast.stmt] = []
        for statement in node.body:
            if isinstance(statement, ast.For):
                replacement = self._rewrite_loop(statement)
                if replacement is not None:
                    setup, loop = replacement
                    new_body.extend((setup, loop))
                    self.changed = True
                    continue
            new_body.append(statement)
        node.body = new_body
        return node

    def _rewrite_loop(self, loop: ast.For) -> tuple[ast.stmt, ast.For] | None:
        matches = [
            call
            for call in ast.walk(loop)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "count"
            and len(call.args) == 1
            and isinstance(call.func.value, ast.Name)
        ]
        if not matches:
            return None
        collection = matches[0].func.value.id
        if any(
            not isinstance(call.func, ast.Attribute)
            or not isinstance(call.func.value, ast.Name)
            or call.func.value.id != collection
            for call in matches
        ):
            return None
        index_name = f"_perf_counts_{self.counter_index}"
        self.counter_index += 1
        setup = ast.Assign(
            targets=[ast.Name(id=index_name, ctx=ast.Store())],
            value=ast.DictComp(
                key=ast.Name(id="_perf_item", ctx=ast.Load()),
                value=ast.Constant(value=0),
                generators=[
                    ast.comprehension(
                        target=ast.Name(id="_perf_item", ctx=ast.Store()),
                        iter=ast.Name(id=collection, ctx=ast.Load()),
                        ifs=[],
                        is_async=0,
                    )
                ],
            ),
        )
        increment = ast.For(
            target=ast.Name(id="_perf_item", ctx=ast.Store()),
            iter=ast.Name(id=collection, ctx=ast.Load()),
            body=[
                ast.AugAssign(
                    target=ast.Subscript(
                        value=ast.Name(id=index_name, ctx=ast.Load()),
                        slice=ast.Name(id="_perf_item", ctx=ast.Load()),
                        ctx=ast.Store(),
                    ),
                    op=ast.Add(),
                    value=ast.Constant(value=1),
                )
            ],
            orelse=[],
        )
        setup = ast.If(
            test=ast.Constant(value=True),
            body=[setup, increment],
            orelse=[],
        )
        replacer = _CountCallReplacer(collection, index_name)
        rewritten_loop = replacer.visit(loop)
        assert isinstance(rewritten_loop, ast.For)
        return setup, rewritten_loop


class _CountCallReplacer(ast.NodeTransformer):
    def __init__(self, collection: str, index_name: str) -> None:
        self.collection = collection
        self.index_name = index_name

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "count"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == self.collection
            and len(node.args) == 1
        ):
            return ast.copy_location(
                ast.Subscript(
                    value=ast.Name(id=self.index_name, ctx=ast.Load()),
                    slice=node.args[0],
                    ctx=ast.Load(),
                ),
                node,
            )
        return node
