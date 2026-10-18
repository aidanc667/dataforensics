"""Descriptive insights about what the data says (not about its quality):
distribution shapes, the strongest associations between columns, group
differences, and trends over time.

Everything here is descriptive and unweighted. With many columns, many
pairs get compared, so some "associations" will be chance -- results are
ranked by effect size (not p-values) and capped, and the app says so.
Missing-value codes passed in `exclude` are never treated as numbers.
Large files are sampled to `_MAX_ROWS` evenly spaced rows to bound cost.
"""

import math
from collections import Counter
from datetime import date

from dataforensics.patterns import _iso, numeric_values

_MAX_ROWS = 20_000
_MIN_PAIR_N = 30
_MAX_COLUMNS = 30
_MIN_GROUP_N = 5
_MAX_LEVELS = 12
_RHO_THRESHOLD = 0.3
_ETA_SQ_THRESHOLD = 0.06
_CRAMERS_V_THRESHOLD = 0.2
_MAX_ASSOCIATIONS = 15


def sample_row_indices(n: int) -> list[int]:
    """Indices of the evenly spaced rows sample_rows keeps (all of them
    when n <= _MAX_ROWS) -- for mapping a sampled row back to its real
    row number."""
    if n <= _MAX_ROWS:
        return list(range(n))
    step = n / _MAX_ROWS
    return [int(i * step) for i in range(_MAX_ROWS)]


def sample_rows(rows: list[dict]) -> list[dict]:
    if len(rows) <= _MAX_ROWS:
        return rows
    step = len(rows) / _MAX_ROWS
    return [rows[int(i * step)] for i in range(_MAX_ROWS)]


def _mean(v: list[float]) -> float:
    return sum(v) / len(v)


def _median(v: list[float]) -> float:
    s = sorted(v)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 100 or x == int(x) else f"{x:,.2f}"


def _shape(skew: float) -> str:
    side = "right" if skew > 0 else "left"
    if abs(skew) < 0.5:
        return "roughly symmetric"
    if abs(skew) < 1:
        return f"moderately {side}-skewed"
    return f"strongly {side}-skewed"


def numeric_summaries(rows: list[dict], columns: list[str], exclude: dict[str, set[str]] | None = None) -> list[dict]:
    """Per numeric column: count, mean, median, standard deviation, min,
    max, skewness and a plain-language shape ("strongly right-skewed":
    a long tail of large values, so the median is the better typical
    value)."""
    exclude = exclude or {}
    out = []
    for col in columns:
        vals = [v for _, v in numeric_values(rows, col, exclude.get(col))]
        if len(vals) < _MIN_PAIR_N:
            continue
        mean = _mean(vals)
        sd = math.sqrt(sum((x - mean) ** 2 for x in vals) / (len(vals) - 1))
        skew = 0.0 if sd == 0 else sum(((x - mean) / sd) ** 3 for x in vals) / len(vals)
        out.append(
            {
                "Column": col,
                "N": len(vals),
                "Mean": round(mean, 2),
                "Median": round(_median(vals), 2),
                "Std dev": round(sd, 2),
                "Min": round(min(vals), 2),
                "Max": round(max(vals), 2),
                "Shape": _shape(skew),
            }
        )
    return out


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (ties share the mean of their positions)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float | None:
    mx, my = _mean(x), _mean(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(x: list[float], y: list[float]) -> float | None:
    """Spearman rank correlation, or None when either side is constant."""
    return _pearson(_ranks(x), _ranks(y))


def eta_squared(groups: dict[str, list[float]]) -> float | None:
    """Share of a numeric column's variance explained by group membership
    (0 = groups identical, 1 = groups fully separate)."""
    all_vals = [v for g in groups.values() for v in g]
    grand = _mean(all_vals)
    total = sum((v - grand) ** 2 for v in all_vals)
    if total == 0:
        return None
    between = sum(len(g) * (_mean(g) - grand) ** 2 for g in groups.values())
    return between / total


def cramers_v(pairs: list[tuple[str, str]]) -> float | None:
    """Cramér's V for two categorical columns (0 = independent, 1 =
    perfectly associated)."""
    n = len(pairs)
    a_counts = Counter(a for a, _ in pairs)
    b_counts = Counter(b for _, b in pairs)
    k = min(len(a_counts), len(b_counts))
    if k < 2:
        return None
    joint = Counter(pairs)
    chi2 = 0.0
    for a, ca in a_counts.items():
        for b, cb in b_counts.items():
            expected = ca * cb / n
            chi2 += (joint.get((a, b), 0) - expected) ** 2 / expected
    return math.sqrt(chi2 / (n * (k - 1)))


def _strength(value: float, cutoffs: tuple[float, float]) -> str:
    return "strong" if value >= cutoffs[1] else "moderate" if value >= cutoffs[0] else "weak"


def find_associations(
    rows: list[dict],
    numeric_columns: list[str],
    categorical_columns: list[str],
    exclude: dict[str, set[str]] | None = None,
    skip_pairs: set[frozenset[str]] | None = None,
) -> list[dict]:
    """The strongest relationships between columns, ranked by effect size:
    numeric-numeric (Spearman rho), category-numeric (eta squared, with
    the group medians spelled out -- this is the "group differences"
    view), category-category (Cramér's V). `skip_pairs` excludes pairs
    already known to be the same information (redundant columns)."""
    exclude = exclude or {}
    skip_pairs = skip_pairs or set()
    rows = sample_rows(rows)
    numeric_columns = numeric_columns[:_MAX_COLUMNS]
    categorical_columns = categorical_columns[:_MAX_COLUMNS]
    num = {c: dict(numeric_values(rows, c, exclude.get(c))) for c in numeric_columns}
    cat: dict[str, dict[int, str]] = {}
    for c in categorical_columns:
        ex = exclude.get(c, set())
        mapping = {i: str(r.get(c, "")).strip() for i, r in enumerate(rows)}
        mapping = {i: v for i, v in mapping.items() if v != "" and v not in ex}
        if 2 <= len(set(mapping.values())) <= _MAX_LEVELS:
            cat[c] = mapping

    found: list[dict] = []
    ncols = list(num)
    for i, a in enumerate(ncols):
        for b in ncols[i + 1:]:
            if frozenset((a, b)) in skip_pairs:
                continue
            shared = sorted(num[a].keys() & num[b].keys())
            if len(shared) < _MIN_PAIR_N:
                continue
            rho = spearman([num[a][k] for k in shared], [num[b][k] for k in shared])
            if rho is not None and abs(rho) >= _RHO_THRESHOLD:
                direction = "rises" if rho > 0 else "falls"
                found.append(
                    {
                        "Columns": f"{a} ↔ {b}",
                        "Measure": "Spearman ρ",
                        "Effect": round(rho, 2),
                        "Strength": _strength(abs(rho), (0.3, 0.6)),
                        "N": len(shared),
                        "Insight": f"As {a} increases, {b} generally {direction} (ρ = {rho:.2f}).",
                        "_rank": abs(rho),
                    }
                )
    for g in cat:
        for x in ncols:
            if frozenset((g, x)) in skip_pairs or g == x:
                continue
            groups: dict[str, list[float]] = {}
            for k, level in cat[g].items():
                if k in num[x]:
                    groups.setdefault(level, []).append(num[x][k])
            groups = {lvl: v for lvl, v in groups.items() if len(v) >= _MIN_GROUP_N}
            n = sum(len(v) for v in groups.values())
            if len(groups) < 2 or n < _MIN_PAIR_N:
                continue
            eta2 = eta_squared(groups)
            if eta2 is None or eta2 < _ETA_SQ_THRESHOLD:
                continue
            medians = sorted(((lvl, _median(v)) for lvl, v in groups.items()), key=lambda lv: lv[1])
            lo, hi = medians[0], medians[-1]
            found.append(
                {
                    "Columns": f"{g} → {x}",
                    "Measure": "η²",
                    "Effect": round(eta2, 2),
                    "Strength": _strength(eta2, (0.06, 0.14)),
                    "N": n,
                    "Insight": f"{x} differs by {g}: median {_fmt(hi[1])} for {g} = {hi[0]} vs {_fmt(lo[1])} for {g} = {lo[0]}.",
                    "_rank": math.sqrt(eta2),
                }
            )
    ccols = list(cat)
    for i, a in enumerate(ccols):
        for b in ccols[i + 1:]:
            if frozenset((a, b)) in skip_pairs:
                continue
            shared_keys = cat[a].keys() & cat[b].keys()
            if len(shared_keys) < _MIN_PAIR_N:
                continue
            pairs = [(cat[a][k], cat[b][k]) for k in shared_keys]
            v = cramers_v(pairs)
            if v is None or v < _CRAMERS_V_THRESHOLD or v >= 0.999:
                continue
            top = Counter(pairs).most_common(1)[0][0]
            found.append(
                {
                    "Columns": f"{a} ↔ {b}",
                    "Measure": "Cramér's V",
                    "Effect": round(v, 2),
                    "Strength": _strength(v, (0.2, 0.4)),
                    "N": len(pairs),
                    "Insight": f"{a} and {b} are related; the most common combination is {a} = {top[0]} with {b} = {top[1]}.",
                    "_rank": v,
                }
            )
    found.sort(key=lambda r: -r["_rank"])
    # Insight first after the column pair, so it reads before the numbers.
    order = ("Columns", "Insight", "Measure", "Effect", "Strength", "N")
    return [{k: r[k] for k in order} for r in found[:_MAX_ASSOCIATIONS]]


def find_trends(
    rows: list[dict], date_columns: list[str], numeric_columns: list[str], exclude: dict[str, set[str]] | None = None
) -> dict:
    """For the first date column with ISO dates spanning 2+ periods:
    record counts per period (year, or month when the data covers under
    two years) and numeric columns that trend over time (|Spearman ρ| >=
    0.3 against the date). Returns {"date_column", "period", "counts":
    [(period, n)], "trends": [{column, rho, direction}]} or {} if no
    usable date column."""
    exclude = exclude or {}
    rows = sample_rows(rows)
    for dcol in date_columns:
        dated = [(i, d) for i, r in enumerate(rows) if (d := _iso(r.get(dcol))) is not None]
        if len(dated) < _MIN_PAIR_N:
            continue
        first, last = min(d for _, d in dated), max(d for _, d in dated)
        by_year = (last - first).days >= 730
        def key(d: date) -> str:
            return str(d.year) if by_year else f"{d.year}-{d.month:02d}"
        counts = sorted(Counter(key(d) for _, d in dated).items())
        if len(counts) < 2:
            continue
        ordinal = {i: d.toordinal() for i, d in dated}
        trends = []
        for col in numeric_columns:
            vals = dict(numeric_values(rows, col, exclude.get(col)))
            shared = sorted(ordinal.keys() & vals.keys())
            if len(shared) < _MIN_PAIR_N:
                continue
            rho = spearman([ordinal[k] for k in shared], [vals[k] for k in shared])
            if rho is not None and abs(rho) >= _RHO_THRESHOLD:
                trends.append({"column": col, "rho": round(rho, 2), "direction": "increasing" if rho > 0 else "decreasing"})
        return {"date_column": dcol, "period": "year" if by_year else "month", "counts": counts, "trends": trends}
    return {}
