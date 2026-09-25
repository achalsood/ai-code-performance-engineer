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
    membership_transformer = _MembershipIndexTransformer()
    rewritten = membership_transformer.visit(tree)
    if membership_transformer.changed:
        ast.fix_missing_locations(rewritten)
        return _Rewrite(
            "Index repeated membership lookups",
            "Builds a set once and reuses it for repeated membership tests inside a loop.",
            "membership-index",
            ast.unparse(rewritten) + "\n",
        )

    count_transformer = _LinearCountTransformer()
    rewritten = count_transformer.visit(tree)
    if count_transformer.changed:
        _ensure_counter_import(rewritten)
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


def _ensure_counter_import(tree: ast.AST) -> None:
    if not isinstance(tree, ast.Module):
        return
    for statement in tree.body:
        if (
            isinstance(statement, ast.ImportFrom)
            and statement.module == "collections"
            and any(alias.name == "Counter" for alias in statement.names)
        ):
            return
    insert_at = 0
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        insert_at = 1
    while insert_at < len(tree.body):
        statement = tree.body[insert_at]
        if not isinstance(statement, ast.ImportFrom) or statement.module != "__future__":
            break
        insert_at += 1
    tree.body.insert(
        insert_at,
        ast.ImportFrom(module="collections", names=[ast.alias(name="Counter")], level=0),
    )


class _MembershipIndexTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.changed = False
        self.index = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        new_body: list[ast.stmt] = []
        for statement_index, statement in enumerate(node.body):
            if isinstance(statement, ast.For):
                replacement = self._rewrite_loop(statement, node.body[:statement_index])
                if replacement is not None:
                    setup, loop = replacement
                    new_body.extend((setup, loop))
                    self.changed = True
                    continue
            new_body.append(statement)
        node.body = new_body
        return node

    def _rewrite_loop(
        self, loop: ast.For, preceding_statements: list[ast.stmt]
    ) -> tuple[ast.Assign, ast.For] | None:
        matches = [
            compare
            for compare in ast.walk(loop)
            if isinstance(compare, ast.Compare)
            and len(compare.ops) == 1
            and isinstance(compare.ops[0], (ast.In, ast.NotIn))
            and len(compare.comparators) == 1
            and isinstance(compare.comparators[0], ast.Name)
        ]
        if not matches:
            return None
        collections = {
            compare.comparators[0].id
            for compare in matches
            if isinstance(compare.comparators[0], ast.Name)
        }
        if len(collections) != 1:
            return None
        collection = next(iter(collections))
        loop_names = {
            child.id for child in ast.walk(loop.target) if isinstance(child, ast.Name)
        }
        if collection in loop_names or _name_or_alias_is_mutated(loop, collection):
            return None
        if not all(
            _membership_probe_is_hash_safe(compare.left, loop) for compare in matches
        ):
            return None
        if not _membership_collection_is_statically_hash_safe(
            preceding_statements, collection
        ):
            return None

        used_names = {
            child.id
            for statement in preceding_statements
            for child in ast.walk(statement)
            if isinstance(child, ast.Name)
        }
        used_names.update(
            child.id for child in ast.walk(loop) if isinstance(child, ast.Name)
        )
        index_name = _fresh_generated_name("_perf_membership_", self.index, used_names)
        self.index = int(index_name.rsplit("_", 1)[1]) + 1
        setup = ast.Assign(
            targets=[ast.Name(id=index_name, ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id="set", ctx=ast.Load()),
                args=[ast.Name(id=collection, ctx=ast.Load())],
                keywords=[],
            ),
        )
        rewritten_loop = _MembershipCollectionReplacer(
            collection, index_name
        ).visit(loop)
        assert isinstance(rewritten_loop, ast.For)
        return setup, rewritten_loop


def _fresh_generated_name(prefix: str, start: int, used_names: set[str]) -> str:
    index = start
    while f"{prefix}{index}" in used_names:
        index += 1
    return f"{prefix}{index}"


def _membership_collection_is_statically_hash_safe(
    preceding_statements: list[ast.stmt], collection: str
) -> bool:
    for statement in reversed(preceding_statements):
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == collection
        ):
            value = statement.value
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                return all(
                    isinstance(element, ast.Constant)
                    and isinstance(
                        element.value,
                        (str, bytes, int, float, complex, bool, type(None)),
                    )
                    for element in value.elts
                )
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "range"
            ):
                return True
            return (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in {"list", "tuple", "set"}
                and len(value.args) == 1
                and isinstance(value.args[0], ast.Call)
                and isinstance(value.args[0].func, ast.Name)
                and value.args[0].func.id == "range"
                and not value.keywords
            )
    return False


def _membership_probe_is_hash_safe(expression: ast.expr, loop: ast.For) -> bool:
    if isinstance(expression, ast.Constant):
        return isinstance(
            expression.value, (str, bytes, int, float, complex, bool, type(None))
        )
    if not isinstance(expression, ast.Name):
        return False
    loop_names = {
        child.id for child in ast.walk(loop.target) if isinstance(child, ast.Name)
    }
    if expression.id not in loop_names:
        return False
    return _loop_iterable_is_statically_hash_safe(loop.iter)


def _loop_iterable_is_statically_hash_safe(iterable: ast.expr) -> bool:
    if isinstance(iterable, (ast.List, ast.Tuple, ast.Set)):
        return all(
            isinstance(element, ast.Constant)
            and isinstance(
                element.value,
                (str, bytes, int, float, complex, bool, type(None)),
            )
            for element in iterable.elts
        )
    return (
        isinstance(iterable, ast.Call)
        and isinstance(iterable.func, ast.Name)
        and iterable.func.id == "range"
    )


class _MembershipCollectionReplacer(ast.NodeTransformer):
    def __init__(self, collection: str, index_name: str) -> None:
        self.collection = collection
        self.index_name = index_name

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if (
            len(node.ops) == 1
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id == self.collection
        ):
            node.comparators[0] = ast.copy_location(
                ast.Name(id=self.index_name, ctx=ast.Load()),
                node.comparators[0],
            )
        return node


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
        if _name_is_mutated(loop, collection):
            return None
        if _name_is_passed_to_unknown_call(loop, collection):
            return None
        index_name = f"_perf_counts_{self.counter_index}"
        self.counter_index += 1
        setup = ast.Assign(
            targets=[ast.Name(id=index_name, ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id="Counter", ctx=ast.Load()),
                args=[ast.Name(id=collection, ctx=ast.Load())],
                keywords=[],
            ),
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
        assignment_target = _direct_assignment_target(loop, call)
        if assignment_target is None:
            return None
        if _name_is_passed_to_unknown_call(loop, source_name, ignored_call=call):
            return None
        if _name_is_mutated_after_assignment(loop, assignment_target, call):
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


def _direct_assignment_target(loop: ast.For, call: ast.Call) -> str | None:
    for statement in loop.body:
        if (
            isinstance(statement, ast.Assign)
            and statement.value is call
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            return statement.targets[0].id
    return None


def _name_is_mutated_after_assignment(loop: ast.For, name: str, call: ast.Call) -> bool:
    assignment_index: int | None = None
    for index, statement in enumerate(loop.body):
        if (
            isinstance(statement, ast.Assign)
            and statement.value is call
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == name
        ):
            assignment_index = index
            break
    if assignment_index is None:
        return True
    remainder = ast.For(
        target=loop.target,
        iter=loop.iter,
        body=loop.body[assignment_index + 1 :],
        orelse=[],
        type_comment=loop.type_comment,
    )
    return _name_or_alias_is_mutated(remainder, name)


def _name_or_alias_is_mutated(loop: ast.For, name: str) -> bool:
    aliases = {name}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(loop):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
                and node.targets[0].id not in aliases
            ):
                aliases.add(node.targets[0].id)
                changed = True
    return any(
        _name_is_mutated(loop, alias) or _name_is_passed_to_unknown_call(loop, alias)
        for alias in aliases
    )


class _SpecificCallReplacer(ast.NodeTransformer):
    def __init__(self, target: ast.Call, replacement: ast.expr) -> None:
        self.target = target
        self.replacement = replacement

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if node is self.target:
            return self.replacement
        return self.generic_visit(node)


def _name_is_passed_to_unknown_call(
    loop: ast.For, name: str, ignored_call: ast.Call | None = None
) -> bool:
    safe_methods = {"append", "get", "setdefault"}
    for node in ast.walk(loop):
        if not isinstance(node, ast.Call):
            continue
        if node is ignored_call:
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
            and node.func.attr in safe_methods
        ):
            continue
        if any(
            isinstance(argument, ast.Name) and argument.id == name
            for argument in (*node.args, *(keyword.value for keyword in node.keywords))
        ):
            return True
    return False


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
            new_body.extend(setup)
            new_body.append(rewritten)
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
        if _name_is_passed_to_unknown_call(outer, inner.iter.id):
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
