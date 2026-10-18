"""Key insights: the most useful, least obvious patterns in a dataset,
written as plain-language findings with the data for a small chart.

Built from results the investigation already computed (associations,
missing-rate gaps, trends) plus a few cheap extra passes (how much more
likely one group is to have an outcome, how concentrated a total is, a
value that dominates a column, a long-tailed distribution). Everything is
descriptive and unweighted: a finding is a lead worth checking, not a
causal claim, and the app says so. Cards are ranked by a 0-1 score so the
strongest, most surprising findings come first.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from dataforensics.insights import _ETA_SQ_THRESHOLD as _ETA_THRESHOLD
from dataforensics.insights import _RHO_THRESHOLD, spearman
from dataforensics.patterns import _iso, numeric_values
from dataforensics.typing_guards import is_pii_like_column

_MAX_CARDS = 12
_MAX_PER_KIND = 4  # so one kind of finding can't fill every slot
_LIFT_MIN = 1.5
_LIFT_MIN_GROUP = 30
_LIFT_MIN_Z = 3.0
_TARGET_MAX_LEVELS = 6
_CONCENTRATION_SHARE = 0.3  # top 10% of rows holding >= 30% of the total (3x their share)
_DOMINANT_SHARE = 0.8
_MAX_LIKELIHOOD_COLUMNS = 25  # bounds the (target x group) pair count on wide files
# Whole name parts only, so "country", "account" or "feedback" don't match.
_AMOUNT_NAME = re.compile(
    r"(^|_)(income|incomes|wage|wages|salary|earnings?|revenue|sales|spend|spending|cost|costs|price|prices|amount|"
    r"payments?|charges?|fees?|count|counts|visits|donations?|balance|budget|population)(_|$)",
    re.IGNORECASE,
)


def _fmt(x: float) -> str:
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:,.0f}" if x == int(x) else f"{x:,.2f}"


def _card(
    kind: str, title: str, detail: str, why: str, score: float, columns: tuple[str, ...], chart: dict | None,
    hidden: str | None = None,
) -> dict:
    return {
        "hidden": hidden,
        "kind": kind,
        "title": title,
        "detail": detail,
        "why": why,
        "score": round(max(0.0, min(score, 1.0)), 3),
        "columns": columns,
        "chart": chart,
    }


def _categories(rows: list[dict], column: str, exclude: set[str]) -> dict[int, str]:
    return {
        i: v for i, r in enumerate(rows)
        if (v := str(r.get(column, "") or "").strip()) and v not in exclude
    }


def find_likelihood_gaps(
    rows: list[dict], categorical_columns: list[str], exclude: dict[str, set[str]] | None = None
) -> list[dict]:
    """"Group A is 2.4x as likely to have outcome B": for each pair of
    category columns, the group whose rate of some outcome is furthest
    above the overall rate. Needs >= 30 rows in the group, a lift of 1.5x
    or more, and a two-proportion z of 3+ against everyone else, so small
    groups' chance swings aren't reported. One finding per column pair."""
    exclude = exclude or {}
    cats = {
        c: _categories(rows, c, exclude.get(c, set()))
        for c in categorical_columns[:_MAX_LIKELIHOOD_COLUMNS]
        if not is_pii_like_column(c)
    }
    found = []
    for target, tvals in cats.items():
        levels = Counter(tvals.values())
        if not 2 <= len(levels) <= _TARGET_MAX_LEVELS:
            continue
        for group, gvals in cats.items():
            if group == target:
                continue
            shared = tvals.keys() & gvals.keys()
            n = len(shared)
            if n < 2 * _LIFT_MIN_GROUP:
                continue
            # One counting pass per pair: outcome counts per group.
            by_group: dict[str, Counter] = defaultdict(Counter)
            for k in shared:
                by_group[gvals[k]][tvals[k]] += 1
            group_sizes = {g: sum(c.values()) for g, c in by_group.items()}
            overall_counts = Counter(tvals[k] for k in shared)
            best: dict | None = None
            for outcome in levels:
                overall = overall_counts[outcome] / n
                if not 0.03 <= overall <= 0.7:
                    continue
                for g, outcome_counts in by_group.items():
                    size = group_sizes[g]
                    if size < _LIFT_MIN_GROUP or size == n:
                        continue
                    rate = outcome_counts[outcome] / size
                    rest_n = n - size
                    rest_rate = (overall * n - rate * size) / rest_n
                    lift = rate / overall
                    pooled = overall
                    se = math.sqrt(pooled * (1 - pooled) * (1 / size + 1 / rest_n))
                    z = (rate - rest_rate) / se if se else 0.0
                    if lift >= _LIFT_MIN and z >= _LIFT_MIN_Z and (best is None or lift > float(best["lift"])):
                        best = {"target": target, "outcome": outcome, "group_column": group, "group": g,
                                "rate": rate, "overall": overall, "lift": lift, "size": size}
            if best:
                best["rates"] = sorted(
                    (
                        (lvl, counts[best["outcome"]] / group_sizes[lvl], group_sizes[lvl])
                        for lvl, counts in by_group.items()
                        if group_sizes[lvl] >= 10
                    ),
                    key=lambda t: -t[1],
                )
                found.append(best)
    return sorted(found, key=lambda f: -f["lift"])


def find_concentration(rows: list[dict], numeric_columns: list[str], exclude: dict[str, set[str]] | None = None) -> list[dict]:
    """For amount-like columns (income, cost, visits, ...): the share of the
    column's total held by the top 10% of rows. Reported when it's 30% or
    more (three times an even share) -- e.g. a few high earners or heavy
    users carrying most of the total."""
    exclude = exclude or {}
    found = []
    for column in numeric_columns:
        if not _AMOUNT_NAME.search(column):
            continue
        values = sorted((v for _, v in numeric_values(rows, column, exclude.get(column))), reverse=True)
        if len(values) < 30 or values[-1] < 0:
            continue
        total = sum(values)
        if total <= 0:
            continue
        top_n = max(1, len(values) // 10)
        share = sum(values[:top_n]) / total
        if share >= _CONCENTRATION_SHARE:
            deciles = []
            size = len(values) / 10
            for d in range(10):
                chunk = values[int(d * size): int((d + 1) * size)]
                deciles.append((f"Top {(d + 1) * 10}%" if d == 0 else f"{d * 10}–{(d + 1) * 10}%", sum(chunk) / total))
            found.append({"column": column, "share": share, "deciles": deciles})
    return sorted(found, key=lambda f: -f["share"])


def find_dominant_values(rows: list[dict], categorical_columns: list[str], exclude: dict[str, set[str]] | None = None) -> list[dict]:
    """Category columns where one value is 80%+ of rows -- comparisons
    across that column rest on a small minority."""
    exclude = exclude or {}
    found = []
    for column in categorical_columns:
        vals = list(_categories(rows, column, exclude.get(column, set())).values())
        if len(vals) < 30:
            continue
        counts = Counter(vals)
        if len(counts) < 2:
            continue
        value, top = counts.most_common(1)[0]
        if top / len(vals) >= _DOMINANT_SHARE:
            found.append({"column": column, "value": value, "share": top / len(vals), "counts": counts.most_common(6)})
    return sorted(found, key=lambda f: -f["share"])


def _period_means(rows: list[dict], date_col: str, value_col: str, by_year: bool, exclude: set[str]) -> list[tuple[str, float]]:
    sums: dict[str, list[float]] = defaultdict(list)
    vals = dict(numeric_values(rows, value_col, exclude))
    for i, r in enumerate(rows):
        d = _iso(r.get(date_col))
        if d is not None and i in vals:
            sums[str(d.year) if by_year else f"{d.year}-{d.month:02d}"].append(vals[i])
    return sorted((p, sum(v) / len(v)) for p, v in sums.items())


# --------------------------------------------------------------------- #
# Telling useful patterns from obvious or misleading ones
# --------------------------------------------------------------------- #
# Name parts too generic to suggest two columns measure the same thing.
_GENERIC_TOKENS = {
    "status", "code", "codes", "type", "flag", "level", "value", "total", "count", "number", "date", "year",
    "rate", "ratio", "score", "index", "group", "category", "name", "years", "months", "days", "current",
    "currently", "personal", "household", "person", "self", "reported", "hours", "minutes", "weeks", "miles",
    "pounds", "dollars", "percent", "amount",
}
# Name parts that say a column is calculated from others.
_DERIVED_TOKENS = {
    "ratio", "per", "pct", "percent", "share", "adjusted", "log", "normalized", "scaled", "avg", "average", "mean",
}
# Name parts that mark a column as a whole that others add up to.
_TOTAL_TOKENS = {"total", "overall", "gross", "combined", "sum", "all"}


def _tokens(column: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", column.lower()) if t]


def _name_stems(column: str) -> set[str]:
    return {t[:5] for t in _tokens(column) if len(t) >= 4 and t not in _GENERIC_TOKENS}


def related_by_definition(a: str, b: str) -> str | None:
    """The shared word stem when one column's name is contained in the
    other's ("total_personal_income" in "wages_income" -> "incom"). Names
    that only share a modifier ("blood_pressure" / "blood_glucose") are
    different measures and pass. A name match alone is not enough to hold
    a pattern back -- see `_definitional_numeric` / `_definitional_category`,
    which also need evidence in the data."""
    sa, sb = _name_stems(a), _name_stems(b)
    if not sa or not sb or not (sa <= sb or sb <= sa):
        return None
    return sorted(sa & sb)[0]


def _part_of_whole(vx: dict[int, float], vy: dict[int, float]) -> bool:
    """One non-negative column is (almost) never larger than the other, the
    way wages never exceed total income."""
    keys = vx.keys() & vy.keys()
    if len(keys) < 30 or any(vx[k] < 0 or vy[k] < 0 for k in keys):
        return False
    x_le_y = sum(vx[k] <= vy[k] for k in keys) / len(keys)
    return x_le_y >= 0.98 or (1 - x_le_y + sum(vx[k] == vy[k] for k in keys) / len(keys)) >= 0.98


def _definitional_numeric(rows: list[dict], x: str, y: str, exclude: dict[str, set[str]], rho: float) -> str | None:
    """Why a strong link between two numeric columns is true by
    construction, or None: the names describe the same thing AND the data
    agrees -- one is part of the other, one is calculated from the other
    (a ratio, rate, ...), or they're near-identical in rank order."""
    stem = related_by_definition(x, y)
    if not stem:
        return None
    tokens = {*_tokens(x), *_tokens(y)}
    if _DERIVED_TOKENS & tokens and abs(rho) >= 0.6:
        return f"one is calculated from the other (both measure '{stem}…')"
    # A part only when one name says it's the whole ("total_income"), so two
    # counts that merely tend to be ordered (ER visits, clinic visits) pass.
    if _TOTAL_TOKENS & tokens and _part_of_whole(
        dict(numeric_values(rows, x, exclude.get(x))), dict(numeric_values(rows, y, exclude.get(y)))
    ):
        return f"one is part of the other (both measure '{stem}…')"
    if abs(rho) >= 0.95:
        return f"they rank rows almost identically (both measure '{stem}…')"
    return None


def _determines(gvals: dict[int, str], tvals: dict[int, str]) -> bool:
    """Knowing the first column's value predicts the second almost
    perfectly: it removes 90%+ of the errors made by always guessing the
    second column's most common value (Goodman-Kruskal lambda), so a
    column that is 97% one value isn't "determined" by everything."""
    keys = gvals.keys() & tvals.keys()
    if len(keys) < 30:
        return False
    by_group: dict[str, Counter] = defaultdict(Counter)
    for k in keys:
        by_group[gvals[k]][tvals[k]] += 1
    majority = Counter(tvals[k] for k in keys).most_common(1)[0][1]
    if majority == len(keys):
        return False
    hits = sum(c.most_common(1)[0][1] for c in by_group.values())
    return (hits - majority) / (len(keys) - majority) >= 0.9


def quantile_bands(
    rows: list[dict], column: str, exclude: set[str] | None = None, n_bands: int = 5
) -> dict[int, int]:
    """Row -> band (0 .. n_bands-1, the column's own quantiles). Empty when
    there are too few values, or they barely vary (one band)."""
    values = numeric_values(rows, column, exclude)
    if len(values) < 20 * n_bands:
        return {}
    ordered = sorted(v for _, v in values)
    cuts = [ordered[len(ordered) * b // n_bands] for b in range(1, n_bands)]
    bands = {i: sum(v >= c for c in cuts) for i, v in values}
    return bands if len(set(bands.values())) >= 2 else {}


def _category_strata(rows: list[dict], column: str, exclude: set[str]) -> dict[int, str]:
    """Row -> category, with categories under 10 rows pooled (a stratum
    needs enough rows for a stable rate)."""
    cats = _categories(rows, column, exclude)
    sizes = Counter(cats.values())
    strata = {k: (v if sizes[v] >= 10 else "\x00other") for k, v in cats.items()}
    return strata if len(set(strata.values())) >= 2 else {}


@dataclass(frozen=True)
class _Confounder:
    """A column a pattern is re-tested against. `coarse` strata compare
    rates (each needs enough rows for a stable rate); `fine` strata compare
    averages and rank links (a measure that rises with age still differs
    inside a wide band)."""
    column: str
    coarse: dict[int, Any]
    fine: dict[int, Any]
    numeric: bool = False

    @property
    def within(self) -> str:
        """How to say "comparing like with like" for this column."""
        return f"within similar {self.column} groups" if self.numeric else f"within the same {self.column}"


def build_confounders(
    rows: list[dict], adjust_for: list[str], continuous_columns: list[str], exclude: dict[str, set[str]]
) -> list[_Confounder]:
    found = []
    for column in dict.fromkeys(adjust_for):
        if column in continuous_columns:
            coarse: dict[int, Any] = quantile_bands(rows, column, exclude.get(column), 5)
            fine: dict[int, Any] = quantile_bands(rows, column, exclude.get(column), 10)
        else:
            coarse = fine = _category_strata(rows, column, exclude.get(column, set()))
        if coarse:
            found.append(_Confounder(column, coarse, fine or coarse, column in continuous_columns))
    return found


# A pattern is only re-tested when most of its rows have a value for the
# confounder: otherwise those rows may not be like the rest of the group.
_COVERAGE = 0.8


def standardized_ratio(
    members: list[int], outcome: dict[int, bool], strata: dict[int, Any]
) -> tuple[float, float] | None:
    """(crude, adjusted) ratios for how much more often `members` have the
    outcome than everyone else, both measured on the same rows. Crude: the
    members' rate / the other rows' rate. Adjusted: the members' rate / the
    rate the other rows in the members' own strata have (indirect
    standardization); 1.0 = no excess once the confounder is accounted
    for. The baseline never includes the members themselves -- a group
    that is most of a stratum would otherwise be compared with itself.
    Members whose stratum has fewer than 10 other rows aren't comparable.
    None if too few members, or too small a share of them, are."""
    member_set = set(members)
    other_hits: dict[Any, list[bool]] = defaultdict(list)
    for k, hit in outcome.items():
        if k in strata and k not in member_set:
            other_hits[strata[k]].append(hit)
    other_rate = {b: sum(h) / len(h) for b, h in other_hits.items() if len(h) >= 10}
    in_outcome = [k for k in members if k in outcome]
    usable = [k for k in in_outcome if k in strata and strata[k] in other_rate]
    if len(usable) < 20 or len(usable) < _COVERAGE * len(in_outcome):
        return None
    others = [hit for b, h in other_hits.items() if b in other_rate for hit in h]
    baseline = sum(others) / len(others)
    observed = sum(outcome[k] for k in usable) / len(usable)
    expected = sum(other_rate[strata[k]] for k in usable) / len(usable)
    if baseline <= 0 or expected <= 0:
        return None
    return observed / baseline, observed / expected


def _within_strata_spearman(
    vx: dict[int, float], vy: dict[int, float], strata: dict[int, Any]
) -> tuple[float, float] | None:
    """(rho, rho within strata), both on the rows that have a stratum. The
    within figure is the n-weighted mean of each stratum's own rho."""
    both = vx.keys() & vy.keys()
    keys = sorted(k for k in both if k in strata)
    if len(keys) < 30 or len(keys) < _COVERAGE * len(both):
        return None
    crude = spearman([vx[k] for k in keys], [vy[k] for k in keys])
    if crude is None:
        return None
    by_stratum: dict[Any, list[int]] = defaultdict(list)
    for k in keys:
        by_stratum[strata[k]].append(k)
    total, weight = 0.0, 0
    for members in by_stratum.values():
        if len(members) >= 10 and (r := spearman([vx[k] for k in members], [vy[k] for k in members])) is not None:
            total += r * len(members)
            weight += len(members)
    return (crude, total / weight) if weight else None


def epsilon_squared(groups: dict[str, list[float]]) -> float | None:
    """Bias-corrected eta squared: (SS_between - (k-1) MS_within) /
    SS_total, floored at 0. Plain eta squared is about (k-1)/(n-1) even
    with no real difference, which in small strata is above the reporting
    floor on its own."""
    values = [v for vs in groups.values() for v in vs]
    n, k = len(values), len(groups)
    if k < 2 or n <= k:
        return None
    grand = sum(values) / n
    ss_between = sum(len(vs) * (sum(vs) / len(vs) - grand) ** 2 for vs in groups.values())
    ss_within = sum((v - sum(vs) / len(vs)) ** 2 for vs in groups.values() for v in vs)
    ss_total = ss_between + ss_within
    if ss_total == 0:
        return None
    return max(0.0, (ss_between - (k - 1) * ss_within / (n - k)) / ss_total)


def _within_strata_eta_squared(
    groups_by_row: dict[int, str], values: dict[int, float], strata: dict[int, Any]
) -> tuple[float, float] | None:
    """(eta squared, eta squared within strata), both bias-corrected
    (`epsilon_squared`) and on the rows that have a stratum, so many small
    strata don't add noise of their own. The within figure is the
    n-weighted mean of each stratum's own value."""
    rows_with_both = [k for k in groups_by_row if k in values]
    covered = [k for k in rows_with_both if k in strata]
    if len(covered) < _COVERAGE * len(rows_with_both):
        return None
    crude_groups: dict[str, list[float]] = defaultdict(list)
    for k in covered:
        crude_groups[groups_by_row[k]].append(values[k])
    crude = epsilon_squared({g: v for g, v in crude_groups.items() if len(v) >= 5})
    if crude is None:
        return None
    total, weight = 0.0, 0
    for stratum in set(strata.values()):
        groups: dict[str, list[float]] = defaultdict(list)
        for k in covered:
            if strata[k] == stratum:
                groups[groups_by_row[k]].append(values[k])
        groups = {g: v for g, v in groups.items() if len(v) >= 5}
        n = sum(len(v) for v in groups.values())
        if len(groups) >= 2 and (e := epsilon_squared(groups)) is not None:
            total += e * n
            weight += n
    return (crude, total / weight) if weight else None


def _age_verdict(crude: float, adjusted: float, floor: float) -> str:
    """"explained" when, comparing like with like, the effect loses at
    least half its size and drops below the size that would have been
    reported at all (`floor`); "partly" when it survives but at most half
    of it remains; else "holds". A strong effect a confounder only shrinks
    stays visible."""
    # Both conditions: a pattern that started near the floor and that the
    # confounder barely changes is not "explained" by it.
    # A pattern that started below the floor must lose three quarters of
    # its size, and one of size 0 can't be "explained" at all.
    share_left = 0.5 if crude >= floor else 0.25
    if crude > 0 and adjusted < floor and adjusted <= share_left * crude:
        return "explained"
    return "partly" if adjusted < 0.5 * crude else "holds"


def _ratio_verdict(crude: float, adjusted: float) -> str:
    # The same 1.5x floor the likelihood search reports at; compared as
    # excess over 1 so "half remains" means half the excess. A pattern that
    # started below the floor (missing-data gaps are found on another
    # measure) must lose three quarters of its excess to be "mostly" gone.
    share_left = 0.5 if crude >= _LIFT_MIN else 0.25
    if adjusted < _LIFT_MIN and (adjusted - 1) <= share_left * (crude - 1):
        return "explained"
    return "partly" if (adjusted - 1) < 0.5 * (crude - 1) else "holds"


def _confounding_summary(results: list[tuple["_Confounder", str, str, str]]) -> tuple[str | None, str]:
    """From (confounder, verdict, short measure, long measure) results: the
    reason to hold the card back (the first confounder that explains it),
    or the sentence to add to its detail."""
    for conf, verdict, _, long in results:
        if verdict == "explained":
            return f"Mostly explained by {conf.column}: {conf.within}, {long}.", ""
    partly = [f"{c.column} ({short})" for c, v, short, _ in results if v == "partly"]
    holds = [f"{c.column} ({short})" for c, v, short, _ in results if v == "holds"]
    note = ""
    if partly:
        note += f" Partly explained by {_and(partly)}."
    if holds:
        note += f" Holds after accounting for {_and(holds)}."
    return None, note


def _times(ratio: float) -> str:
    """"2.4×"; ratios against a near-zero rate are huge and meaningless as
    numbers, so anything past 20x reads "20×+"."""
    return "20×+" if ratio > 20 else f"{ratio:.1f}×"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def _missing_cell(value: Any, codes: set[str]) -> bool:
    v = str(value or "").strip()
    return v == "" or v in codes


class _Labels:
    """Codebook labels for coded values: "3" -> "3 (Widowed)". Codes are
    also matched without leading zeros, so a codebook's "01" labels "1"."""

    def __init__(self, value_labels: dict[str, dict[str, str]] | None):
        self._map: dict[str, dict[str, str]] = {}
        for column, labels in (value_labels or {}).items():
            m = self._map.setdefault(column, {})
            for code, label in labels.items():
                m[code] = label
                if code.isdecimal():
                    m.setdefault(str(int(code)), label)

    def label(self, column: str, value: str) -> str | None:
        m = self._map.get(column)
        if not m:
            return None
        return m.get(value) or (m.get(str(int(value))) if value.isdecimal() else None)

    def text(self, column: str, value: str) -> str:
        """For sentences: "3 (Widowed)"."""
        lbl = self.label(column, value)
        return f"{value} ({lbl})" if lbl else value

    def short(self, column: str, value: str) -> str:
        """For chart axes: "3 · Widowed"."""
        lbl = self.label(column, value)
        return f"{value} · {lbl}" if lbl else value

    def group(self, text: str, column: str) -> str:
        """Relabel a "column = value" group string from the missing-data
        patterns; anything else (a numeric split) is returned unchanged."""
        prefix = f"{column} = "
        return prefix + self.text(column, text[len(prefix):]) if text.startswith(prefix) else text


def build_key_insights(
    *,
    rows: list[dict],
    continuous_columns: list[str],
    grouping_columns: list[str],
    exclude_codes: dict[str, set[str]],
    associations: list[dict],
    group_missingness: list[dict],
    trends: dict,
    numeric_summary: list[dict],
    adjust_for: list[str] | None = None,
    value_labels: dict[str, dict[str, str]] | None = None,
) -> list[dict]:
    """Ranked insight cards (most useful first, at most `_MAX_CARDS`, at
    most two per column pair so one strong relationship can't crowd out
    everything else). Each card: kind, title, detail, why, score, columns,
    chart ({"type": "bar"|"line"|"scatter", "data": [...], "x", "y",
    "percent": bool} or None), and hidden (None, or the reason the card is
    held back: true by definition, or mostly explained by a confounder).

    Each pattern is re-tested within groups of each `adjust_for` column
    (age and sex by default, from the pipeline): one that mostly
    disappears is hidden as "mostly explained by <column>", one that
    survives says so -- the commonest way an automated pattern misleads is
    by being age in disguise (widowed -> diabetes). `value_labels`
    ({column: {code: label}}, from a codebook) turn "3" into "3 (Widowed)"."""
    cards: list[dict] = []
    labels = _Labels(value_labels)
    confounders = build_confounders(rows, adjust_for or [], continuous_columns, exclude_codes)
    category_cache: dict[str, dict[int, str]] = {}
    numeric_cache: dict[str, dict[int, float]] = {}

    def categories(column: str) -> dict[int, str]:
        if column not in category_cache:
            category_cache[column] = _categories(rows, column, exclude_codes.get(column, set()))
        return category_cache[column]

    def numbers(column: str) -> dict[int, float]:
        if column not in numeric_cache:
            numeric_cache[column] = dict(numeric_values(rows, column, exclude_codes.get(column)))
        return numeric_cache[column]

    def applicable(*columns: str) -> list[_Confounder]:
        return [c for c in confounders if c.column not in columns]

    for f in find_likelihood_gaps(rows, grouping_columns, exclude_codes):
        hidden = None
        detail = f"{f['rate']:.0%} of this group vs {f['overall']:.0%} overall ({f['size']:,} rows)."
        gvals, tvals = categories(f["group_column"]), categories(f["target"])
        stem = related_by_definition(f["group_column"], f["target"])
        if stem and (_determines(gvals, tvals) or _determines(tvals, gvals)):
            hidden = f"Likely true by definition — one column determines the other (both are about '{stem}…')."
        else:
            outcome = {k: v == f["outcome"] for k, v in tvals.items() if k in gvals}
            members = [k for k, v in gvals.items() if v == f["group"]]
            results = []
            for conf in applicable(f["group_column"], f["target"]):
                ratios = standardized_ratio(members, outcome, conf.coarse)
                if ratios is not None:
                    # Judged on its own scale (the group vs the rest); shown on
                    # the title's (vs the overall rate) by keeping the share
                    # of the excess that survives.
                    crude, adj = ratios
                    shown = max(0.0, 1 + (f["lift"] - 1) * (adj - 1) / (crude - 1)) if crude > 1 else adj
                    results.append((conf, _ratio_verdict(crude, adj), _times(shown), f"only {_times(shown)} as likely"))
            hidden, note = _confounding_summary(results)
            detail += note
        cards.append(_card(
            "Higher likelihood",
            f"{f['group_column']} = {labels.text(f['group_column'], f['group'])} are {f['lift']:.1f}× as likely "
            f"to have {f['target']} = {labels.text(f['target'], f['outcome'])}",
            detail,
            "Points to a segment that behaves very differently — worth targeting, checking, or controlling for.",
            # Ranked by how big the difference is in absolute terms, with the
            # ratio as a tiebreaker: 30% -> 60% matters more than 3% -> 10%.
            0.35 + min(f["rate"] - f["overall"], 0.4) + min(f["lift"] - 1, 3) / 12,
            (f["group_column"], f["target"]),
            {"type": "bar", "x": f["group_column"],
             "y": f"% with {f['target']} = {labels.short(f['target'], f['outcome'])}", "percent": True,
             "data": [{"label": labels.short(f["group_column"], lvl), "value": rate} for lvl, rate, _ in f["rates"][:8]]},
            hidden,
        ))

    # Several columns missing for the same group (a skip pattern) are one
    # finding, not several: merge them onto the strongest gap.
    merged: dict[tuple[str, str], dict] = {}
    for g in group_missingness:
        key = (g["group_column"], g["highest"]["group"])
        if key in merged:
            merged[key]["also"].append(g["column"])
        else:
            merged[key] = {**g, "also": []}
    all_columns = list(rows[0]) if rows else []

    def skip_levels(g: dict) -> list[str] | None:
        """The gate levels when every level of a category is blank for
        (nearly) everyone or (nearly) no one -- "smoked 100 cigarettes = no"
        (and "don't know") skip the follow-up question."""
        if g["kind"] != "category" or g["highest"]["rate"] < 0.95 or g["lowest"]["rate"] > 0.02:
            return None
        if any(0.05 < x["rate"] < 0.95 for x in g["groups"]):
            return None
        return [x["group"][len(g["group_column"]) + 3:] for x in g["groups"] if x["rate"] >= 0.95]

    # A skip gate explains the column's other missing-data patterns: people
    # in good health skip "currently smokes" more only because more of them
    # never smoked. Each gate is used as a confounder for its columns.
    skip_gates: dict[str, list[_Confounder]] = defaultdict(list)
    for g in merged.values():
        if skip_levels(g):
            strata = _category_strata(rows, g["group_column"], set())
            if strata:
                for column in (g["column"], *g["also"]):
                    skip_gates[column].append(_Confounder(g["group_column"], strata, strata))

    for g in list(merged.values())[:6]:
        hi, lo = g["highest"], g["lowest"]
        gcol = g["group_column"]
        names = [g["column"], *g["also"]]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        hi_text, lo_text = labels.group(hi["group"], gcol), labels.group(lo["group"], gcol)
        level = hi["group"][len(gcol) + 3:] if g["kind"] == "category" else None
        chart = {"type": "bar", "x": gcol, "y": f"% missing {g['column']}", "percent": True,
                 "data": [{"label": labels.short(gcol, x["group"][len(gcol) + 3:]) if g["kind"] == "category" else x["group"],
                           "value": x["rate"]}
                          for x in sorted(g["groups"], key=lambda x: -x["rate"])[:8]]}
        gate_levels = skip_levels(g)
        if gate_levels:
            # Blank for (nearly) everyone given some answers and no one given
            # the others. A designed skip if these rows answered the rest of
            # the survey; if they're blank elsewhere too, they may have
            # stopped answering.
            gate_members = {k for k, v in categories(gcol).items() if v in gate_levels}
            hi_text = f"{gcol} = " + " or ".join(labels.text(gcol, lvl) for lvl in gate_levels)
            other_cols = [c for c in all_columns if c not in names and c != gcol]
            in_group = [_missing_cell(rows[k].get(c), exclude_codes.get(c, set())) for k in gate_members for c in other_cols]
            outside = [_missing_cell(r.get(c), exclude_codes.get(c, set()))
                       for i, r in enumerate(rows) if i not in gate_members for c in other_cols]
            group_blank = sum(in_group) / len(in_group) if in_group else 0.0
            outside_blank = sum(outside) / len(outside) if outside else 0.0
            if group_blank <= max(0.1, 2 * outside_blank):
                cards.append(_card(
                    "Skip pattern",
                    f"{listed} {'is' if len(names) == 1 else 'are'} blank whenever {hi_text}",
                    f"Missing for 95%+ of {hi_text} and at most 5% for every other answer, while these rows "
                    f"answered the other questions ({group_blank:.0%} blank elsewhere) — most likely a question "
                    "skipped by design.",
                    "If the question was skipped by design, treat these blanks as 'not applicable' rather than "
                    "missing answers — imputing them would invent data.",
                    0.18,
                    (g["column"], gcol),
                    chart,
                ))
            else:
                cards.append(_card(
                    "Missing data pattern",
                    f"Rows with {hi_text} are blank in {listed} — and much of everything else",
                    f"Missing for 95%+ of {hi_text}, and these rows are blank in {group_blank:.0%} of the "
                    f"other columns too (vs {outside_blank:.0%} for everyone else). That looks like people who "
                    "stopped answering (a break-off) or a block of questions that doesn't apply to them — check "
                    "the questionnaire.",
                    "Break-offs are missing answers, not 'not applicable' — dropping or imputing them changes "
                    "results differently, so it matters which this is.",
                    0.4 + g["gap"] * 0.6,
                    (g["column"], gcol),
                    chart,
                ))
            continue
        hidden = None
        detail = (
            f"Missing for {hi['rate']:.0%} of {hi_text} vs {lo['rate']:.0%} of {lo_text}"
            + (f" (shown for {g['column']})." if len(names) > 1 else ".")
        )
        if level is not None:
            members_list: list[int] | None = [k for k, v in categories(gcol).items() if v == level]
        elif g.get("cut") is not None:
            # A numeric split ("age_years < 45"): rebuild the high group from
            # the cut, so it's re-tested like any other group.
            below = hi["group"].startswith(f"{gcol} <")
            members_list = [k for k, v in numbers(gcol).items() if (v < g["cut"]) == below]
        else:
            members_list = None
        if members_list is not None:
            target_codes = exclude_codes.get(g["column"], set())
            missing = {i: _missing_cell(r.get(g["column"]), target_codes) for i, r in enumerate(rows)}
            results = []
            gates = [c for c in skip_gates.get(g["column"], []) if c.column != gcol]
            for conf in [*gates, *applicable(gcol, *names)]:
                ratios = standardized_ratio(members_list, missing, conf.coarse)
                if ratios is not None:
                    results.append((conf, _ratio_verdict(*ratios), f"{_times(ratios[1])} as often as the rest",
                                    f"only {_times(ratios[1])} as often missing as the rest"))
            hidden, note = _confounding_summary(results)
            detail += note
        cards.append(_card(
            "Missing data pattern",
            f"{listed} {'is' if len(names) == 1 else 'are'} missing far more often for {hi_text}",
            detail,
            "Dropping incomplete rows would quietly under-represent this group and bias any result.",
            # A split on a measured variable ("age < 35") reads more plainly
            # than a coded category, so it gets a small boost.
            0.4 + g["gap"] * 0.6 + (0.08 if g["kind"] == "numeric_split" else 0.0),
            (g["column"], gcol),
            chart,
            hidden,
        ))

    # Recover the column pair from the display label by matching real column
    # names, never by splitting on the arrow -- a column name may contain one.
    # Only labels that actually occur are resolved, so this stays linear in
    # the number of associations rather than quadratic in the column count.
    labels_present = {a["Columns"] for a in associations}
    numeric_pairs = {
        f"{x} ↔ {y}": (x, y)
        for x in continuous_columns
        if any(lbl.startswith(f"{x} ↔ ") for lbl in labels_present)
        for y in continuous_columns
        if f"{x} ↔ {y}" in labels_present
    }
    group_pairs = {
        f"{g} → {x}": (g, x)
        for g in grouping_columns
        if any(lbl.startswith(f"{g} → ") for lbl in labels_present)
        for x in continuous_columns
        if f"{g} → {x}" in labels_present
    }
    for a in associations:
        if a["Measure"] == "Spearman ρ" and a["Columns"] in numeric_pairs:
            x, y = numeric_pairs[a["Columns"]]
            vals_x, vals_y = numbers(x), numbers(y)
            pts = [{"x": vals_x[k], "y": vals_y[k]} for k in sorted(vals_x.keys() & vals_y.keys())[:600]]
            direction = "higher" if a["Effect"] > 0 else "lower"
            hidden = None
            detail = f"Spearman ρ = {a['Effect']:+.2f} across {a['N']:,} rows ({a['Strength']})."
            why_definitional = _definitional_numeric(rows, x, y, exclude_codes, a["Effect"])
            if why_definitional:
                hidden = f"Likely true by definition — {why_definitional}."
            else:
                results = []
                for conf in applicable(x, y):
                    rhos = _within_strata_spearman(vals_x, vals_y, conf.fine)
                    if rhos is not None:
                        # Floor: the same |ρ| a relationship needs to be reported.
                        verdict = _age_verdict(abs(rhos[0]), abs(rhos[1]), _RHO_THRESHOLD)
                        results.append((conf, verdict, f"ρ = {rhos[1]:+.2f}", f"ρ falls to {rhos[1]:+.2f}"))
                hidden, note = _confounding_summary(results)
                detail += note
            cards.append(_card(
                "Relationship",
                f"Higher {x} goes with {direction} {y}",
                detail,
                "A strong link between two measures — one may help predict, explain, or sanity-check the other.",
                0.3 + abs(a["Effect"]) * 0.6,
                (x, y),
                {"type": "scatter", "x": x, "y": y, "percent": False, "data": pts},
                hidden,
            ))
        elif a["Measure"] == "η²" and a["Columns"] in group_pairs:
            grp, x = group_pairs[a["Columns"]]
            groups: dict[str, list[float]] = defaultdict(list)
            vals = numbers(x)
            for k, lvl in categories(grp).items():
                if k in vals:
                    groups[lvl].append(vals[k])
            medians = sorted(
                ((lvl, sorted(v)[len(v) // 2]) for lvl, v in groups.items() if len(v) >= 5), key=lambda t: -t[1]
            )
            if len(medians) >= 2:
                hidden = None
                score = 0.3 + math.sqrt(a["Effect"]) * 0.6
                detail = (
                    f"Median {_fmt(medians[0][1])} for {grp} = {labels.text(grp, medians[0][0])} vs "
                    f"{_fmt(medians[-1][1])} for {grp} = {labels.text(grp, medians[-1][0])} (η² = {a['Effect']:.2f})."
                )
                stem = related_by_definition(grp, x)
                if stem and a["Effect"] >= 0.9:
                    hidden = f"Likely true by definition — one column determines the other (both are about '{stem}…')."
                elif any(c.column == x for c in confounders):
                    # Groups differing in age (retired people are older) mostly
                    # describe who is in each group -- real, but expected.
                    score *= 0.6
                else:
                    results = []
                    for conf in applicable(grp, x):
                        etas = _within_strata_eta_squared(categories(grp), vals, conf.fine)
                        # Below the floor once corrected for small-sample bias,
                        # the difference is too close to noise to re-test.
                        if etas is not None and etas[0] >= _ETA_THRESHOLD:
                            # Floor: a "medium" effect, the smallest reported.
                            verdict = _age_verdict(*etas, _ETA_THRESHOLD)
                            results.append((conf, verdict, f"bias-corrected η² {etas[0]:.2f} → {etas[1]:.2f}",
                                            f"bias-corrected η² falls from {etas[0]:.2f} to {etas[1]:.2f}"))
                    hidden, note = _confounding_summary(results)
                    detail += note
                cards.append(_card(
                    "Group difference",
                    f"{x} differs sharply by {grp}",
                    detail,
                    f"Any average of {x} depends heavily on the mix of {grp} — compare like with like.",
                    score,
                    (grp, x),
                    {"type": "bar", "x": grp, "y": f"Median {x}", "percent": False,
                     "data": [{"label": labels.short(grp, lvl), "value": m} for lvl, m in medians[:8]]},
                    hidden,
                ))

    for c in find_concentration(rows, continuous_columns, exclude_codes):
        cards.append(_card(
            "Concentration",
            f"The top 10% of rows hold {c['share']:.0%} of all {c['column']}",
            "Three or more times an even share — a small group accounts for much of the total.",
            "Averages hide this; a few records drive the total, so check them first and consider medians.",
            0.35 + (c["share"] - _CONCENTRATION_SHARE) * 0.8,
            (c["column"],),
            {"type": "bar", "x": "Rows, ranked high to low", "y": f"Share of total {c['column']}", "percent": True,
             "data": [{"label": lbl, "value": v} for lbl, v in c["deciles"]]},
        ))

    if trends and trends.get("trends"):
        by_year = trends["period"] == "year"
        for t in trends["trends"][:3]:
            series = _period_means(rows, trends["date_column"], t["column"], by_year, exclude_codes.get(t["column"], set()))
            if len(series) >= 2:
                cards.append(_card(
                    "Trend",
                    f"{t['column']} is {t['direction']} over time",
                    f"Average {t['column']} went from {_fmt(series[0][1])} ({series[0][0]}) to {_fmt(series[-1][1])} "
                    f"({series[-1][0]}); Spearman ρ = {t['rho']:+.2f} against {trends['date_column']}.",
                    "A drift over time can be a real change — or a change in how the data was collected.",
                    0.35 + abs(t["rho"]) * 0.5,
                    (t["column"], trends["date_column"]),
                    {"type": "line", "x": trends["period"].title(), "y": f"Average {t['column']}", "percent": False,
                     "data": [{"label": p, "value": v} for p, v in series]},
                ))
    if trends and len(trends.get("counts", [])) >= 3:
        counts = [n for _, n in trends["counts"]]
        if max(counts) >= 2 * max(1, min(counts)):
            cards.append(_card(
                "Trend",
                f"Record volume varies a lot by {trends['period']}",
                f"From {min(counts):,} to {max(counts):,} records per {trends['period']} in {trends['date_column']}.",
                "Uneven coverage means some periods are much better represented than others.",
                0.3,
                (trends["date_column"],),
                {"type": "line", "x": trends["period"].title(), "y": "Records", "percent": False,
                 "data": [{"label": p, "value": n} for p, n in trends["counts"]]},
            ))

    for d in find_dominant_values(rows, grouping_columns, exclude_codes)[:3]:
        cards.append(_card(
            "Dominant value",
            f"{d['share']:.0%} of {d['column']} is a single value: {labels.text(d['column'], d['value'])}",
            f"The other values together make up only {1 - d['share']:.0%} of rows.",
            f"Comparisons across {d['column']} rest on a small minority — results for the rare values are fragile.",
            0.2 + (d["share"] - _DOMINANT_SHARE),
            (d["column"],),
            {"type": "bar", "x": d["column"], "y": "Rows", "percent": False,
             "data": [{"label": labels.short(d["column"], v), "value": n} for v, n in d["counts"]]},
        ))

    for s in numeric_summary:
        if s["Shape"].startswith("strongly") and s["Mean"] and s["Median"] is not None:
            side = "right" if "right" in s["Shape"] else "left"
            cards.append(_card(
                "Distribution",
                f"{s['Column']} has a long {side} tail",
                f"Mean {_fmt(s['Mean'])} vs median {_fmt(s['Median'])} (range {_fmt(s['Min'])}–{_fmt(s['Max'])}).",
                "The average is pulled by extreme values; the median is the better 'typical' figure here.",
                0.25,
                (s["Column"],),
                None,
            ))

    return select_cards(cards)


def select_cards(cards: list[dict]) -> list[dict]:
    """The shown cards (best first, within the caps) followed by the
    held-back ones (deduplicated, at most `_MAX_CARDS`)."""
    held_back = sorted((c for c in cards if c["hidden"]), key=lambda c: -c["score"])
    cards = [c for c in cards if not c["hidden"]]
    cards.sort(key=lambda c: -c["score"])
    picked: list[dict] = []
    per_pair: Counter = Counter()
    per_kind_pair: set[tuple[str, frozenset]] = set()
    per_kind: Counter = Counter()
    for c in cards:
        pair = frozenset(c["columns"])
        # At most two cards per column pair, and one per kind (so A -> B
        # and B -> A "higher likelihood" don't both appear).
        if per_pair[pair] >= 2 or (c["kind"], pair) in per_kind_pair or per_kind[c["kind"]] >= _MAX_PER_KIND:
            continue
        per_kind_pair.add((c["kind"], pair))
        per_kind[c["kind"]] += 1
        per_pair[pair] += 1
        picked.append(c)
        if len(picked) == _MAX_CARDS:
            break
    # A pair whose other direction is shown (A -> B holds within age bands,
    # B -> A doesn't) is reported by that card, which says it holds; listing
    # the other as "held back" too would contradict it. Only cards actually
    # shown count -- one cut by the caps mustn't make its pair vanish.
    seen: set[tuple[str, frozenset]] = {(c["kind"], frozenset(c["columns"])) for c in picked}
    hidden_cards: list[dict] = []
    for c in held_back:
        if (c["kind"], frozenset(c["columns"])) not in seen and len(hidden_cards) < _MAX_CARDS:
            seen.add((c["kind"], frozenset(c["columns"])))
            hidden_cards.append(c)
    return picked + hidden_cards
