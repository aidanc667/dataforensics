"""User-declared expectations: rules the analyst states about the data
("age is 0-120", "id is unique", "sex is one of M/F/U", "if sex = M then
pregnant must be blank") checked against every row.

Inferred checks find what looks wrong; expectations check what the
analyst KNOWS must be true -- including survey skip logic and impossible
combinations, which can't be inferred from the data alone. Every spec is
validated up front (a malformed rule raises ValueError instead of
silently checking nothing), and results report how many rows each rule
couldn't evaluate, so "0 violations" is never claimed for a rule that
never actually ran.
"""

from collections import Counter

from dataforensics.typing_guards import is_pii_like_column, parse_finite_float

EXPECTATION_TYPES = ("range", "required", "unique", "allowed_values", "conditional")
OPERATORS = ("=", "!=", "<", "<=", ">", ">=", "is blank", "is not blank")
_NUMERIC_OPS = {"<", "<=", ">", ">="}
_EVIDENCE_LIMIT = 10
_MASK = "[masked]"


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def validate_expectation(spec: dict, columns: list[str]) -> None:
    """Raise ValueError if `spec` is malformed or names an unknown column."""
    kind = spec.get("type")
    if kind not in EXPECTATION_TYPES:
        raise ValueError(f"unknown expectation type {kind!r}; expected one of {EXPECTATION_TYPES}")
    needed = ["if_column", "then_column"] if kind == "conditional" else ["column"]
    for field in needed:
        if spec.get(field) not in columns:
            raise ValueError(f"{kind} expectation: {field} {spec.get(field)!r} is not a column in this dataset")
    if kind == "range":
        lo, hi = spec.get("min"), spec.get("max")
        if lo is None and hi is None:
            raise ValueError("range expectation needs a min, a max, or both")
        for bound in (lo, hi):
            if bound is not None and (isinstance(bound, bool) or not isinstance(bound, (int, float))):
                raise ValueError(f"range bounds must be numbers, got {bound!r}")
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"range min {lo} is greater than max {hi}")
    if kind == "allowed_values":
        values = spec.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError("allowed_values expectation needs a non-empty list of values")
    if kind == "conditional":
        for side in ("if", "then"):
            op = spec.get(f"{side}_op")
            if op not in OPERATORS:
                raise ValueError(f"conditional {side}_op {op!r} must be one of {OPERATORS}")
            if op not in ("is blank", "is not blank"):
                value = spec.get(f"{side}_value")
                if value is None or _text(value) == "":
                    raise ValueError(f"conditional {side}_op {op!r} needs a {side}_value")
                if op in _NUMERIC_OPS and parse_finite_float(_text(value)) is None:
                    raise ValueError(f"conditional {side}_op {op!r} needs a numeric {side}_value, got {value!r}")


def describe(spec: dict) -> str:
    kind = spec["type"]
    if kind == "range":
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and hi is not None:
            return f"{spec['column']} is between {lo} and {hi}"
        return f"{spec['column']} is {'≥ ' + str(lo) if lo is not None else '≤ ' + str(hi)}"
    if kind == "required":
        return f"{spec['column']} is never blank"
    if kind == "unique":
        return f"{spec['column']} is unique"
    if kind == "allowed_values":
        return f"{spec['column']} is one of: {', '.join(map(str, spec['values']))}"

    def clause(side: str) -> str:
        op = spec[f"{side}_op"]
        col = spec[f"{side}_column"]
        return f"{col} {op}" if op in ("is blank", "is not blank") else f"{col} {op} {spec[f'{side}_value']}"

    return f"if {clause('if')} then {clause('then')}"


def _compare(raw: str, op: str, target) -> bool | None:
    """True/False, or None when a numeric operator meets a non-number."""
    if op == "is blank":
        return raw == ""
    if op == "is not blank":
        return raw != ""
    if op in ("=", "!="):
        equal = raw == _text(target)
        return equal if op == "=" else not equal
    left, right = parse_finite_float(raw), parse_finite_float(_text(target))
    if left is None or right is None:
        return None
    return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[op]


def _shown(column: str, raw: str) -> str:
    return _MASK if is_pii_like_column(column) else raw


def evaluate_expectation(rows: list[dict], spec: dict) -> dict:
    """{"rule", "violations", "not_evaluable", "evaluated", "evidence":
    [(row_index, explanation)]}. A blank cell never violates range or
    allowed_values (blankness is what `required` is for); a non-numeric
    value in a range column counts as not evaluable, not as passing."""
    kind = spec["type"]
    violations: list[tuple[int, str]] = []
    not_evaluable = 0
    evaluated = 0
    if kind == "unique":
        col = spec["column"]
        counts = Counter(_text(r.get(col)) for r in rows if _text(r.get(col)) != "")
        for i, r in enumerate(rows):
            v = _text(r.get(col))
            if v == "":
                continue
            evaluated += 1
            if counts[v] > 1:
                violations.append((i, f"{col} = {_shown(col, v)} appears {counts[v]} times"))
    else:
        for i, r in enumerate(rows):
            if kind == "required":
                col = spec["column"]
                evaluated += 1
                if _text(r.get(col)) == "":
                    violations.append((i, f"{col} is blank"))
            elif kind == "range":
                col = spec["column"]
                raw = _text(r.get(col))
                if raw == "":
                    continue
                num = parse_finite_float(raw)
                if num is None:
                    not_evaluable += 1
                    continue
                evaluated += 1
                lo, hi = spec.get("min"), spec.get("max")
                if (lo is not None and num < lo) or (hi is not None and num > hi):
                    violations.append((i, f"{col} = {_shown(col, raw)}"))
            elif kind == "allowed_values":
                col = spec["column"]
                raw = _text(r.get(col))
                if raw == "":
                    continue
                evaluated += 1
                if raw not in {_text(v) for v in spec["values"]}:
                    violations.append((i, f"{col} = {_shown(col, raw)}"))
            else:
                if_raw = _text(r.get(spec["if_column"]))
                cond = _compare(if_raw, spec["if_op"], spec.get("if_value"))
                if cond is None:
                    not_evaluable += 1
                    continue
                if not cond:
                    continue
                then_raw = _text(r.get(spec["then_column"]))
                ok = _compare(then_raw, spec["then_op"], spec.get("then_value"))
                if ok is None:
                    not_evaluable += 1
                    continue
                evaluated += 1
                if not ok:
                    violations.append(
                        (
                            i,
                            (
                                f"{spec['if_column']} = {_shown(spec['if_column'], if_raw)}, "
                                f"{spec['then_column']} = {_shown(spec['then_column'], then_raw) or '(blank)'}"
                            ),
                        )
                    )
    return {
        "rule": describe(spec),
        "violations": len(violations),
        "not_evaluable": not_evaluable,
        "evaluated": evaluated,
        "evidence": violations[:_EVIDENCE_LIMIT],
    }


def evaluate_expectations(rows: list[dict], specs: list[dict], columns: list[str]) -> list[dict]:
    """Validate every spec, then evaluate each. Raises ValueError on the
    first malformed spec, naming it."""
    for n, spec in enumerate(specs, start=1):
        try:
            validate_expectation(spec, columns)
        except ValueError as exc:
            raise ValueError(f"expectation #{n}: {exc}") from exc
    return [evaluate_expectation(rows, spec) for spec in specs]
