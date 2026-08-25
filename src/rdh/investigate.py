"""Pre-rules heuristic investigation: pure functions that SUGGEST candidate
findings a human can review and approve, without requiring a rules file up
front and without ever mutating data themselves.

This module exists to power an interactive "investigate first, then decide
what to fix" workflow (e.g. the Streamlit app). It deliberately does not
touch `validation.py`'s `validate()` — that function's severity-tier
accounting has been hardened across many review rounds for the CLI's
rules-driven path, and this module's heuristics are a different thing:
suggestions generated *before* any rule exists, always requiring explicit
human approval before they become an actual rule.
"""

from itertools import combinations

from rdh.validation import is_ambiguous_date

_COMMON_SENTINEL_STRINGS = {
    "-99", "-9", "99", "999", "9999",
    "n/a", "na", "n.a.", "unknown", "unk",
    "refused", "dk", "don't know", "not applicable", ".",
}

_MAX_CARDINALITY_FOR_FUZZY_MATCH = 50


def detect_duplicate_rows(rows: list[dict]) -> list[dict]:
    """Exact full-row duplicates (every field identical to an earlier row).

    This does not require a declared primary key -- it's a generic,
    conservative check usable before any rules exist. Returns one entry per
    duplicate row found (not per group), each naming which earlier row it
    duplicates.
    """
    seen: dict[tuple, int] = {}
    duplicates = []
    for i, row in enumerate(rows):
        key = tuple(sorted(row.items()))
        if key in seen:
            duplicates.append({"row_index": i, "duplicate_of_row_index": seen[key], "row": row})
        else:
            seen[key] = i
    return duplicates


def detect_candidate_sentinels(rows: list[dict], columns: list[str]) -> dict[str, list[str]]:
    """Values that look like common research/survey missing-value codes
    (e.g. "-99", "N/A", "Refused") appearing literally in the data.

    Never claims these ARE sentinels -- only that they match a common
    naming convention and are worth a human decision (map to a specific
    missing-value label, or leave as a legitimate value).
    """
    found: dict[str, list[str]] = {}
    for col in columns:
        values = {str(row.get(col, "")).strip() for row in rows if row.get(col) not in (None, "")}
        hits = sorted(v for v in values if v.casefold() in _COMMON_SENTINEL_STRINGS)
        if hits:
            found[col] = hits
    return found


def detect_ambiguous_date_columns(rows: list[dict], columns: list[str]) -> dict[str, int]:
    """Columns containing MM/DD-vs-DD/MM-ambiguous date-shaped strings,
    detected heuristically without requiring a `type: date` rule declared
    up front. Reuses validation.py's exact ambiguity check for consistency.
    """
    found: dict[str, int] = {}
    for col in columns:
        count = sum(
            1 for row in rows
            if row.get(col) and is_ambiguous_date(str(row[col]).strip())
        )
        if count:
            found[col] = count
    return found


def detect_similar_categories(values: list[str], threshold: int = 85) -> list[dict]:
    """Cluster near-duplicate category values within one column (e.g.
    "Male" / "male" / "MALE") using rapidfuzz string similarity.

    Skips columns with more than `_MAX_CARDINALITY_FOR_FUZZY_MATCH` unique
    values -- those are free-text/ID-shaped, not categorical, and an O(n^2)
    pairwise comparison there is neither meaningful nor cheap.

    Returns one entry per cluster (2+ similar values), each with a
    suggested canonical form and a confidence level: "high" when every
    value in the cluster is identical after case/whitespace normalization
    (a safe, near-certain merge), "medium" when they're merely fuzzy-close
    (a human should look before approving).
    """
    from rapidfuzz import fuzz

    unique_values = sorted({v for v in values if v and v.strip()})
    if len(unique_values) > _MAX_CARDINALITY_FOR_FUZZY_MATCH:
        return []

    normalized = {v: v.strip().casefold() for v in unique_values}
    parent: dict[str, str] = {v: v for v in unique_values}

    def find(v: str) -> str:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    exact_after_normalize: set[frozenset] = set()
    for a, b in combinations(unique_values, 2):
        if normalized[a] == normalized[b]:
            union(a, b)
            exact_after_normalize.add(frozenset((a, b)))
        elif fuzz.ratio(normalized[a], normalized[b]) >= threshold:
            union(a, b)

    groups: dict[str, list[str]] = {}
    for v in unique_values:
        groups.setdefault(find(v), []).append(v)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        all_exact = all(
            frozenset((a, b)) in exact_after_normalize or normalized[a] == normalized[b]
            for a, b in combinations(members, 2)
        )
        clusters.append(
            {
                "values": sorted(members),
                "suggested_canonical": sorted(members)[0],
                "confidence": "high" if all_exact else "medium",
            }
        )
    return clusters
