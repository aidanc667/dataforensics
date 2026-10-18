"""Additional data-quality checks: number heaping, missingness rates that
differ by group, redundant columns, rare categories, age that disagrees
with date of birth, and totals that don't equal the sum of their parts.

Same contract as investigate.py: detection only, evidence included,
nothing modified. Numeric parsing skips any value in `exclude` (the
candidate missing-value codes the investigation already found), so a
9999 code is never mistaken for a real measurement here.
"""

import math
import re
from collections import Counter
from datetime import date

from dataforensics.typing_guards import is_pii_like_column, parse_finite_float

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def numeric_values(rows: list[dict], column: str, exclude: set[str] | None = None) -> list[tuple[int, float]]:
    """(row_index, value) for every cell in `column` that parses as a
    finite number and isn't one of the `exclude`d codes."""
    exclude = exclude or set()
    out = []
    for i, row in enumerate(rows):
        raw = str(row.get(column, "")).strip()
        if raw == "" or raw in exclude:
            continue
        parsed = parse_finite_float(raw)
        if parsed is not None:
            out.append((i, parsed))
    return out


def _median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


# --------------------------------------------------------------------- #
# Number heaping
# --------------------------------------------------------------------- #
_HEAPING_MIN_VALUES = 50
_HEAPING_MIN_RANGE = 20
_HEAPING_EXPECTED_SHARE = 0.2  # 2 of 10 final digits are 0 or 5
_HEAPING_FLAG_SHARE = 0.4  # twice the expected share


def detect_heaping(
    rows: list[dict], columns: list[str], dictionary: dict, exclude: dict[str, set[str]] | None = None
) -> dict[str, dict]:
    """Integer-valued measurement columns where values ending in 0 or 5
    are far more common than chance -- the signature of self-reported or
    estimated values ("about 40", "150 lb") rather than measured ones.

    Only whole-number columns (>= 90% integers) spanning at least
    `_HEAPING_MIN_RANGE` units with >= `_HEAPING_MIN_VALUES` values of 10
    or more are examined; codes and small counts can't heap. A column
    already rounded to tens/hundreds/thousands is measured at that
    resolution. Flags when
    the 0/5 share is >= 40% (uniform final digits give 20%). Also reports
    the Whipple-style index (share / 0.2 * 100; 100 = no heaping).
    """
    exclude = exclude or {}
    results: dict[str, dict] = {}
    for column in columns:
        if dictionary.get(column, {}).get("category") != "free_text":
            continue
        values = [v for _, v in numeric_values(rows, column, exclude.get(column))]
        if not values:
            continue
        integers = [int(v) for v in values if v == int(v)]
        if len(integers) < 0.9 * len(values):
            continue
        # Measure at the column's own resolution: incomes recorded to the
        # nearest $1,000 are all multiples of 5 trivially, so the question
        # is whether the THOUSANDS digit heaps (50k, 60k), not the units.
        resolution = next((r for r in (1000, 100, 10) if sum(1 for v in integers if v % r == 0) >= 0.95 * len(integers)), 1)
        eligible = [v // resolution for v in integers if v % resolution == 0 and abs(v // resolution) >= 10]
        if len(eligible) < _HEAPING_MIN_VALUES or max(eligible) - min(eligible) < _HEAPING_MIN_RANGE:
            continue
        heaped = [v for v in eligible if v % 5 == 0]
        share = len(heaped) / len(eligible)
        if share >= _HEAPING_FLAG_SHARE:
            top = Counter(v * resolution for v in heaped).most_common(5)
            results[column] = {
                "share": share,
                "expected_share": _HEAPING_EXPECTED_SHARE,
                "index": round(share / _HEAPING_EXPECTED_SHARE * 100),
                "eligible_count": len(eligible),
                "resolution": resolution,
                "heaped_count": len(heaped),
                "top_values": [(str(v), c) for v, c in top],
            }
    return results


def heaping_share_text(finding: dict) -> str:
    """What the heaping share measures, in words: "76% of values end in 0
    or 5", or -- for a column recorded in thousands, where every value ends
    in 0 -- "91% of values are multiples of 5,000"."""
    resolution = finding.get("resolution", 1)
    if resolution == 1:
        return f"{finding['share']:.0%} of values end in 0 or 5"
    return f"{finding['share']:.0%} of values are multiples of {5 * resolution:,}"


# --------------------------------------------------------------------- #
# Missingness rates by group
# --------------------------------------------------------------------- #
_GROUP_MIN_SIZE = 20
_GROUP_MIN_GAP = 0.15  # 15 percentage points
_GROUP_MIN_RATIO = 1.5
_GROUP_MAX_LEVELS = 10
_GROUP_MIN_Z = 3.0


def _is_missing(raw, exclude: set[str]) -> bool:
    text = str(raw if raw is not None else "").strip()
    return text == "" or text in exclude


def detect_group_missingness(
    rows: list[dict],
    target_columns: list[str],
    categorical_columns: list[str],
    numeric_columns: list[str],
    exclude: dict[str, set[str]] | None = None,
) -> list[dict]:
    """For each column with missing values, the missing RATE across groups
    of another column: each level of a categorical column, or below/above
    the median of a numeric column. A target counts as missing when empty
    or holding one of its `exclude`d missing-value codes.

    Reported when the highest and lowest group rates differ by >= 15
    percentage points AND by a factor of >= 1.5, with every compared group
    holding >= 20 rows -- e.g. "income missing in 31% of age >= 65 vs 9%
    of age < 65" -- and the two-proportion z statistic between them is
    >= 3, which screens out gaps that small groups produce by chance. That
    is a noise filter, not a hypothesis test (many pairs are compared);
    a finding says the data may not be missing at random, not why.
    """
    exclude = exclude or {}
    results: list[dict] = []
    for target in target_columns:
        target_ex = exclude.get(target, set())
        missing_flags = [_is_missing(row.get(target), target_ex) for row in rows]
        if not any(missing_flags) or all(missing_flags):
            continue
        for group_col in categorical_columns:
            if group_col == target or is_pii_like_column(group_col):
                continue
            group_ex = exclude.get(group_col, set())
            buckets: dict[str, list[bool]] = {}
            for row, missing in zip(rows, missing_flags, strict=True):
                level = str(row.get(group_col, "")).strip()
                if level == "" or level in group_ex:
                    continue
                buckets.setdefault(level, []).append(missing)
            groups = [(f"{group_col} = {lvl}", sum(f) / len(f), len(f)) for lvl, f in buckets.items() if len(f) >= _GROUP_MIN_SIZE]
            if 2 <= len(groups) <= _GROUP_MAX_LEVELS:
                _maybe_add(results, target, group_col, "category", groups)
        for group_col in numeric_columns:
            if group_col == target or is_pii_like_column(group_col):
                continue
            parsed = numeric_values(rows, group_col, exclude.get(group_col))
            if len(parsed) < 2 * _GROUP_MIN_SIZE:
                continue
            cut = _median([v for _, v in parsed])
            below = [missing_flags[i] for i, v in parsed if v < cut]
            above = [missing_flags[i] for i, v in parsed if v >= cut]
            if len(below) < _GROUP_MIN_SIZE or len(above) < _GROUP_MIN_SIZE:
                continue
            cut_label = f"{cut:,.0f}" if cut == int(cut) else f"{cut:,.2f}"
            groups = [
                (f"{group_col} < {cut_label}", sum(below) / len(below), len(below)),
                (f"{group_col} ≥ {cut_label}", sum(above) / len(above), len(above)),
            ]
            _maybe_add(results, target, group_col, "numeric_split", groups, cut)
    return sorted(results, key=lambda r: -r["gap"])


def _two_proportion_z(p1: float, n1: int, p2: float, n2: int) -> float:
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    return math.inf if se == 0 else (p1 - p2) / se


def _maybe_add(
    results: list[dict], target: str, group_col: str, kind: str, groups: list[tuple[str, float, int]],
    cut: float | None = None,
) -> None:
    hi = max(groups, key=lambda g: g[1])
    lo = min(groups, key=lambda g: g[1])
    gap = hi[1] - lo[1]
    # The z filter screens out gaps small groups produce by chance (the
    # error-injection benchmark found ~1 such pair per 300-row file).
    if (
        gap >= _GROUP_MIN_GAP
        and (lo[1] == 0 or hi[1] / lo[1] >= _GROUP_MIN_RATIO)
        and _two_proportion_z(hi[1], hi[2], lo[1], lo[2]) >= _GROUP_MIN_Z
    ):
        results.append(
            {
                "column": target,
                "group_column": group_col,
                "kind": kind,
                "highest": {"group": hi[0], "rate": hi[1], "n": hi[2]},
                "lowest": {"group": lo[0], "rate": lo[1], "n": lo[2]},
                "gap": gap,
                "groups": [{"group": g, "rate": r, "n": n} for g, r, n in groups],
                # numeric_split: the median the groups were split at
                # ("< cut" / ">= cut"), so callers can rebuild the groups.
                "cut": cut,
            }
        )


# --------------------------------------------------------------------- #
# Redundant columns
# --------------------------------------------------------------------- #
def detect_redundant_columns(rows: list[dict], columns: list[str], dictionary: dict) -> list[dict]:
    """Groups of columns that carry the same information: identical
    values on every row, or a one-to-one recoding (each value of one
    always pairs with exactly one value of the other, e.g. state_code and
    state_name). Columns with a single value, or with distinct values on
    more than half the rows, are skipped -- near-unique columns are
    trivially one-to-one with each other.

    Not an error: a code and its label are often kept on purpose. Worth
    knowing before analysis, where including both double-counts a factor.
    Runs in one pass per column (a canonical first-seen-order signature),
    not pairwise, so it stays linear in columns x rows.
    """
    n = len(rows)
    by_signature: dict[tuple, list[str]] = {}
    raw_by_column: dict[str, tuple] = {}
    for column in columns:
        unique = dictionary.get(column, {}).get("unique_count", 0)
        # Near-unique columns (names, timestamps) pair one-to-one with each
        # other by coincidence, so only repeated-value columns count.
        if unique <= 1 or unique > n / 2:
            continue
        values = tuple(str(row.get(column, "")).strip() for row in rows)
        first_seen: dict[str, int] = {}
        signature = tuple(first_seen.setdefault(v, len(first_seen)) for v in values)
        by_signature.setdefault(signature, []).append(column)
        raw_by_column[column] = values
    groups = []
    for cols in by_signature.values():
        if len(cols) < 2:
            continue
        identical = all(raw_by_column[c] == raw_by_column[cols[0]] for c in cols[1:])
        groups.append({"columns": cols, "relationship": "identical" if identical else "one-to-one recoding"})
    return groups


# --------------------------------------------------------------------- #
# Rare categories
# --------------------------------------------------------------------- #
_RARE_MIN_VALUES = 50
_RARE_MAX_COUNT = 2
_RARE_MAX_SHARE = 0.01


def detect_rare_categories(
    rows: list[dict], columns: list[str], dictionary: dict, exclude: dict[str, set[str]] | None = None
) -> dict[str, list[tuple[str, int]]]:
    """Categorical columns (>= 50 values, >= 3 levels) with levels that
    appear only once or twice and under 1% of the time -- often a typo or
    a stray code, and too small to analyze as their own group either way.
    Missing-value codes are excluded (they're flagged separately)."""
    exclude = exclude or {}
    results: dict[str, list[tuple[str, int]]] = {}
    for column in columns:
        if dictionary.get(column, {}).get("category") != "categorical":
            continue
        ex = exclude.get(column, set())
        values = [str(row.get(column, "")).strip() for row in rows]
        values = [v for v in values if v != "" and v not in ex]
        if len(values) < _RARE_MIN_VALUES:
            continue
        counts = Counter(values)
        if len(counts) < 3:
            continue
        rare = sorted(
            ((v, c) for v, c in counts.items() if c <= _RARE_MAX_COUNT and c / len(values) <= _RARE_MAX_SHARE),
            key=lambda vc: (vc[1], vc[0]),
        )
        if rare:
            results[column] = rare
    return results


# --------------------------------------------------------------------- #
# Age vs. date of birth
# --------------------------------------------------------------------- #
_AGE_TOLERANCE_YEARS = 1
_AGE_REFERENCE_NAME = re.compile(r"visit|interview|exam|survey|admi|enrol|intake|screen", re.IGNORECASE)


def _iso(value) -> date | None:
    text = str(value or "").strip()
    if not _ISO_DATE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _completed_years(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def detect_age_dob_mismatch(rows: list[dict], role_by_column: dict) -> dict[tuple[str, str, str], list[tuple[int, str, int]]]:
    """For an AGE column plus a DATE_OF_BIRTH column and a reference DATE
    column (the date the age was recorded, e.g. a visit date), rows where
    the recorded age differs from the age implied by the two dates by more
    than one year. Only ISO (YYYY-MM-DD) dates are used -- an ambiguous
    MM/DD vs DD/MM date can't be trusted for arithmetic. Without a
    reference date the check doesn't apply (there's no "as of" date).

    Returns {(age_col, dob_col, ref_col): [(row_index, recorded_age, implied_age)]}.
    """
    def cols(role: str) -> list[str]:
        return [c for c, r in role_by_column.items() if r and r.get("role") == role]

    results: dict[tuple[str, str, str], list[tuple[int, str, int]]] = {}
    ages, dobs, refs = cols("AGE"), cols("DATE_OF_BIRTH"), cols("DATE")
    if not (ages and dobs and refs):
        return results
    # Prefer a column that records when the age was taken (a visit,
    # interview, exam or admission) over e.g. an updated_at timestamp.
    preferred = [c for c in refs if _AGE_REFERENCE_NAME.search(c)]
    age_col, dob_col, ref_col = ages[0], dobs[0], (preferred or refs)[0]
    evidence = []
    for i, row in enumerate(rows):
        recorded = parse_finite_float(str(row.get(age_col, "")).strip())
        born, on = _iso(row.get(dob_col)), _iso(row.get(ref_col))
        if recorded is None or born is None or on is None or on < born:
            continue
        implied = _completed_years(born, on)
        if abs(recorded - implied) > _AGE_TOLERANCE_YEARS:
            evidence.append((i, str(row.get(age_col)).strip(), implied))
    if evidence:
        results[(age_col, dob_col, ref_col)] = evidence
    return results


def age_dob_check_applies(role_by_column: dict) -> bool:
    roles = {r.get("role") for r in role_by_column.values() if r}
    return {"AGE", "DATE_OF_BIRTH", "DATE"} <= roles


# --------------------------------------------------------------------- #
# Totals that don't add up
# --------------------------------------------------------------------- #
_TOTAL_NAME = re.compile(r"(^|_)total(_|$)", re.IGNORECASE)
_TOTAL_MIN_ROWS = 10
_TOTAL_MIN_AGREEMENT = 0.8


def find_total_candidates(columns: list[str]) -> dict[str, list[str]]:
    """{total_column: [component columns]} by naming: a column named like
    "total_income" or "income_total" and 2+ other columns containing its
    stem ("income"), e.g. "wage_income" and "other_income"."""
    candidates: dict[str, list[str]] = {}
    for column in columns:
        if not _TOTAL_NAME.search(column):
            continue
        stem = _TOTAL_NAME.sub("_", column).strip("_").lower()
        if len(stem) < 3:
            continue
        parts = [c for c in columns if c != column and stem in c.lower() and not _TOTAL_NAME.search(c)]
        if len(parts) >= 2:
            candidates[column] = parts
    return candidates


def detect_total_mismatch(rows: list[dict], columns: list[str], exclude: dict[str, set[str]] | None = None) -> dict[str, dict]:
    """For each total/component group from find_total_candidates whose
    sum relationship is confirmed -- at least 80% of the >= 10 fully-
    numeric rows DO add up, so two columns that merely share a word
    aren't treated as a broken sum -- the rows where total != sum of
    components (tolerance: 0.01, or 0.1% of the total). A confirmed group
    with no mismatches is returned with empty `evidence`, so callers can
    tell "checked and fine" from "never confirmed"."""
    exclude = exclude or {}
    results: dict[str, dict] = {}
    for total_col, parts in find_total_candidates(columns).items():
        evaluable = 0
        agree = 0
        evidence = []
        for i, row in enumerate(rows):
            vals = []
            for c in [total_col, *parts]:
                raw = str(row.get(c, "")).strip()
                parsed = None if raw in exclude.get(c, set()) else parse_finite_float(raw)
                if parsed is None:
                    break
                vals.append(parsed)
            else:
                evaluable += 1
                total, parts_sum = vals[0], sum(vals[1:])
                if abs(total - parts_sum) <= max(0.01, 0.001 * abs(total)):
                    agree += 1
                else:
                    evidence.append((i, total, parts_sum))
        if evaluable >= _TOTAL_MIN_ROWS and agree / evaluable >= _TOTAL_MIN_AGREEMENT:
            results[total_col] = {"components": parts, "evaluable": evaluable, "agree": agree, "evidence": evidence}
    return results
