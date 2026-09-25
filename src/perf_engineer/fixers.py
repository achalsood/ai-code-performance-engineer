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
    count_transformer = _LinearCountTransformer()
    rewritten = count_transformer.visit(tree)
    if count_transformer.changed:
        ast.fix_missing_locations(rewritten)
        return _Rewrite(
            "Precompute repeated counts",
            "Replaces repeated list.count calls in a loop with one frequency table.",
            "data-structure-index",
            ast.unparse(rewritten) + "\n",
        )

    hoist_transformer = _InvariantAllocationTransformer()
    rewritten = hoist_transformer.visit(tree)
    if hoist_transformer.changed:
        ast.fix_missing_locations(rewritten)
        return _Rewrite(
            "Hoist invariant loop work",
            "Moves an invariant sorted() or list() allocation outside a loop.",
            "hoist-invariant-work",
            ast.unparse(rewritten) + "\n",
        )

    return None


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
        first_function = matches[0].func
        if not isinstance(first_function, ast.Attribute) or not isinstance(
            first_function.value, ast.Name
        ):
            return None
        collection = first_function.value.id
        for call in matches:
            function = call.func
            if (
                not isinstance(function, ast.Attribute)
                or not isinstance(function.value, ast.Name)
                or function.value.id != collection
            ):
                return None
        index_name = f"_perf_counts_{self.counter_index}"
        self.counter_index += 1
        count_map = ast.Assign(
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
        setup: ast.stmt = ast.If(
            test=ast.Constant(value=True),
            body=[count_map, increment],
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


class _InvariantAllocationTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.changed = False
        self.hoist_index = 0

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

    def _rewrite_loop(self, loop: ast.For) -> tuple[ast.Assign, ast.For] | None:
        calls = [
            call
            for call in ast.walk(loop)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in {"sorted", "list"}
            and len(call.args) == 1
            and not call.keywords
            and isinstance(call.args[0], ast.Name)
        ]
        if len(calls) != 1:
            return None
        call = calls[0]
        source_argument = call.args[0]
        if not isinstance(source_argument, ast.Name):
            return None
        source_name = source_argument.id
        loop_names = {
            child.id
            for child in ast.walk(loop.target)
            if isinstance(child, ast.Name)
        }
        if source_name in loop_names or _name_is_mutated(loop, source_name):
            return None

        hoisted_name = f"_perf_invariant_{self.hoist_index}"
        self.hoist_index += 1
        setup = ast.Assign(
            targets=[ast.Name(id=hoisted_name, ctx=ast.Store())],
            value=call,
        )
        replacement = ast.copy_location(
            ast.Name(id=hoisted_name, ctx=ast.Load()),
            call,
        )
        rewritten_loop = _SpecificCallReplacer(call, replacement).visit(loop)
        assert isinstance(rewritten_loop, ast.For)
        return setup, rewritten_loop


class _SpecificCallReplacer(ast.NodeTransformer):
    def __init__(self, target: ast.Call, replacement: ast.expr) -> None:
        self.target = target
        self.replacement = replacement

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if node is self.target:
            return self.replacement
        return self.generic_visit(node)


def _name_is_mutated(loop: ast.For, name: str) -> bool:
    mutating_methods = {
        "append",
        "clear",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "sort",
    }
    for node in ast.walk(loop):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(child, ast.Name) and child.id == name
                for target in targets
                for child in ast.walk(target)
            ):
                return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
            and node.func.attr in mutating_methods
        ):
            return True
    return False
