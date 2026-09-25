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

    lookup_transformer = _BatchedNestedLookupTransformer()
    rewritten = lookup_transformer.visit(tree)
    if not lookup_transformer.changed:
        return None
    ast.fix_missing_locations(rewritten)
    return _Rewrite(
        "Index repeated nested lookup",
        "Builds a lookup dictionary once and reuses it across all outer-loop queries.",
        "nested-loop-index",
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


class _BatchedNestedLookupTransformer(ast.NodeTransformer):
    """Index a narrow nested-loop append pattern while preserving first-match semantics."""

    def __init__(self) -> None:
        self.changed = False
        self.index = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        new_body: list[ast.stmt] = []
        for statement in node.body:
            replacement = (
                self._rewrite_outer_loop(statement)
                if isinstance(statement, ast.For)
                else None
            )
            if replacement is None:
                new_body.append(statement)
                continue
            setup, rewritten = replacement
            new_body.extend((setup, rewritten))
            self.changed = True
        node.body = new_body
        return node

    def _rewrite_outer_loop(self, outer: ast.For) -> tuple[list[ast.stmt], ast.For] | None:
        if not isinstance(outer.target, ast.Name) or len(outer.body) != 1:
            return None
        inner = outer.body[0]
        if not isinstance(inner, ast.For) or not isinstance(inner.target, ast.Name):
            return None
        if not isinstance(inner.iter, ast.Name) or inner.orelse or len(inner.body) != 1:
            return None
        condition = inner.body[0]
        if not isinstance(condition, ast.If) or condition.orelse or len(condition.body) != 2:
            return None
        append_statement, break_statement = condition.body
        if not isinstance(append_statement, ast.Expr) or not isinstance(break_statement, ast.Break):
            return None
        append_call = append_statement.value
        if (
            not isinstance(append_call, ast.Call)
            or not isinstance(append_call.func, ast.Attribute)
            or append_call.func.attr != "append"
            or not isinstance(append_call.func.value, ast.Name)
            or len(append_call.args) != 1
            or not isinstance(append_call.args[0], ast.Name)
            or append_call.args[0].id != inner.target.id
        ):
            return None
        keys = _lookup_keys(condition.test, inner.target.id, outer.target.id)
        if keys is None or _name_is_mutated(outer, inner.iter.id):
            return None
        record_key, query_key = keys
        index_name = f"_perf_lookup_{self.index}"
        self.index += 1
        setup = _first_match_index(index_name, inner.target.id, inner.iter.id, record_key)
        lookup_name = f"_perf_match_{self.index}"
        lookup = ast.Assign(
            targets=[ast.Name(id=lookup_name, ctx=ast.Store())],
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id=index_name, ctx=ast.Load()),
                    attr="get",
                    ctx=ast.Load(),
                ),
                args=[_key_subscript(outer.target.id, query_key)],
                keywords=[],
            ),
        )
        append = ast.If(
            test=ast.Compare(
                left=ast.Name(id=lookup_name, ctx=ast.Load()),
                ops=[ast.IsNot()],
                comparators=[ast.Constant(value=None)],
            ),
            body=[
                ast.Expr(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Name(id=append_call.func.value.id, ctx=ast.Load()),
                            attr="append",
                            ctx=ast.Load(),
                        ),
                        args=[ast.Name(id=lookup_name, ctx=ast.Load())],
                        keywords=[],
                    )
                )
            ],
            orelse=[],
        )
        rewritten = ast.For(
            target=outer.target,
            iter=outer.iter,
            body=[lookup, append],
            orelse=outer.orelse,
            type_comment=outer.type_comment,
        )
        return setup, ast.copy_location(rewritten, outer)


def _lookup_keys(test: ast.expr, record: str, query: str) -> tuple[str, str] | None:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    if not isinstance(test.ops[0], ast.Eq) or len(test.comparators) != 1:
        return None
    pairs = (
        (_subscript_key(test.left, record), _subscript_key(test.comparators[0], query)),
        (_subscript_key(test.comparators[0], record), _subscript_key(test.left, query)),
    )
    return next(
        (
            (record_key, query_key)
            for record_key, query_key in pairs
            if record_key and query_key
        ),
        None,
    )


def _subscript_key(expression: ast.expr, variable: str) -> str | None:
    if (
        isinstance(expression, ast.Subscript)
        and isinstance(expression.value, ast.Name)
        and expression.value.id == variable
        and isinstance(expression.slice, ast.Constant)
        and isinstance(expression.slice.value, str)
    ):
        return expression.slice.value
    return None


def _key_subscript(variable: str, key: str) -> ast.Subscript:
    return ast.Subscript(
        value=ast.Name(id=variable, ctx=ast.Load()),
        slice=ast.Constant(value=key),
        ctx=ast.Load(),
    )


def _first_match_index(
    index_name: str, record_name: str, records_name: str, record_key: str
) -> list[ast.stmt]:
    initialize = ast.Assign(
        targets=[ast.Name(id=index_name, ctx=ast.Store())],
        value=ast.Dict(keys=[], values=[]),
    )
    populate = ast.For(
        target=ast.Name(id=record_name, ctx=ast.Store()),
        iter=ast.Name(id=records_name, ctx=ast.Load()),
        body=[
            ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id=index_name, ctx=ast.Load()),
                        attr="setdefault",
                        ctx=ast.Load(),
                    ),
                    args=[
                        _key_subscript(record_name, record_key),
                        ast.Name(id=record_name, ctx=ast.Load()),
                    ],
                    keywords=[],
                )
            )
        ],
        orelse=[],
    )
    return [initialize, populate]
