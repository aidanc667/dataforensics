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

Every confidence signal in this module is qualitative ("high"/"medium"),
never a fabricated percentage. A number like "97% confidence" implies a
calibrated statistical model backing it; nothing here is that model, and
inventing false precision would be exactly the kind of overclaiming this
project's entire design philosophy exists to reject.
"""

import hashlib
import json
import re
from itertools import combinations

from dataforensics.validation import is_ambiguous_date

COMMON_SENTINEL_STRINGS = {
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
        hits = sorted(v for v in values if v.casefold() in COMMON_SENTINEL_STRINGS)
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


def find_sentinel_evidence(rows: list[dict], column: str, sentinel_value: str) -> list[int]:
    """Real row indices where `column` equals `sentinel_value` (after the
    same strip() detect_candidate_sentinels itself applies), for showing
    genuine evidence rows instead of just "this value appears somewhere.\""""
    return [i for i, row in enumerate(rows) if str(row.get(column, "")).strip() == sentinel_value]


def find_ambiguous_date_evidence(rows: list[dict], column: str) -> list[tuple[int, str]]:
    """Real (row_index, raw_value) pairs for the ambiguous-date-shaped
    values detect_ambiguous_date_columns counted for `column`."""
    return [
        (i, str(row[column]).strip())
        for i, row in enumerate(rows)
        if row.get(column) and is_ambiguous_date(str(row[column]).strip())
    ]


def find_category_value_evidence(rows: list[dict], column: str, value: str) -> list[int]:
    """Real row indices where `column` equals `value` -- for showing which
    rows a detect_similar_categories cluster member actually came from."""
    return [i for i, row in enumerate(rows) if row.get(column) == value]


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


# --------------------------------------------------------------------- #
# Semantic role inference — conservative, boundary-aware, never a
# fabricated confidence percentage. A column that matches nothing here
# is simply left unlabeled, which is the common (and correct) case.
# --------------------------------------------------------------------- #

_ROLE_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("AGE", re.compile(r"(^|_)age(_|$)", re.IGNORECASE)),
    ("SEX_OR_GENDER", re.compile(r"(^|_)(sex|gender)(_|$)", re.IGNORECASE)),
    ("DATE", re.compile(r"(^|_)(date|dt|visit_?date|dob)(_|$)", re.IGNORECASE)),
    ("WEIGHT", re.compile(r"(^|_)weight(_kg|_lb|_lbs)?(_|$)", re.IGNORECASE)),
    ("HEIGHT", re.compile(r"(^|_)height(_cm|_in)?(_|$)", re.IGNORECASE)),
    ("INCOME", re.compile(r"(^|_)(income|salary|earnings)(_|$)", re.IGNORECASE)),
    ("RACE_OR_ETHNICITY", re.compile(r"(^|_)(race|ethnicity)(_|$)", re.IGNORECASE)),
]


def infer_semantic_role(column_name: str, dictionary_entry: dict) -> dict | None:
    """Suggest a likely research-variable role for a column, from its name
    and its already-computed profile (dtype/category/range), for the human
    to confirm or dismiss -- never auto-applied to anything.

    Returns None (no suggestion) far more often than it returns a match --
    that's intentional. A column with no matching pattern is not evidence
    of anything; it just means this module has nothing defensible to say
    about it.
    """
    if dictionary_entry.get("category") in ("id",):
        return None  # already classified with higher confidence elsewhere

    for role, pattern in _ROLE_PATTERNS:
        if pattern.search(column_name):
            evidence = f"column name matches the '{role}' naming convention"
            confidence = "medium"
            if role == "AGE" and dictionary_entry.get("category") == "free_text":
                # a numeric-shaped column matching /age/ is a stronger signal
                # than a categorical one (e.g. "usage" contains no boundary
                # match, but "age_group" would be categorical, not numeric --
                # still plausible, just slightly less certain than raw AGE)
                confidence = "high"
            return {"role": role, "confidence": confidence, "evidence": evidence}
    return None


# --------------------------------------------------------------------- #
# Dataset fingerprinting — stateless by design. This module never writes
# a fingerprint to disk itself; the caller (e.g. the app) offers the
# current fingerprint as a download and accepts a previous one as an
# upload to compare against. That keeps this tool exactly as stateless
# as the rest of dataforensics (no server-side history, no cross-session storage,
# no risk of one dataset's structure leaking into another session).
# --------------------------------------------------------------------- #


def compute_dataset_fingerprint(dictionary: dict, row_count: int) -> dict:
    """A schema fingerprint (column names + dtypes + categories -- the
    *structure*) and a value fingerprint (per-column stats -- the observed
    *distribution*), each a SHA-256 hex digest over canonical JSON.

    Never hashes raw values themselves -- only already-aggregated
    statistics already present in the data dictionary (non_null_pct,
    unique_count, category, dtype), so this can't leak PII even for a
    masked/PII-like column.
    """
    schema_shape = sorted(
        (col, fields.get("dtype"), fields.get("category")) for col, fields in dictionary.items()
    )
    value_shape = sorted(
        (
            col,
            fields.get("non_null_pct"),
            fields.get("unique_count"),
            fields.get("is_zero_variance"),
        )
        for col, fields in dictionary.items()
    )
    schema_fingerprint = hashlib.sha256(json.dumps(schema_shape, sort_keys=True).encode()).hexdigest()
    value_fingerprint = hashlib.sha256(json.dumps(value_shape, sort_keys=True).encode()).hexdigest()
    return {
        "schema_fingerprint": schema_fingerprint,
        "value_fingerprint": value_fingerprint,
        "row_count": row_count,
        "column_count": len(dictionary),
        "columns": sorted(dictionary.keys()),
    }


def compare_fingerprints(previous: dict, current: dict, prev_dictionary: dict, curr_dictionary: dict) -> dict:
    """Structured diff between two fingerprints of (presumably) the same
    dataset at different points in time: columns added/removed, row-count
    delta, and per-column missingness/cardinality drift for columns present
    in both.
    """
    prev_cols = set(previous.get("columns", []))
    curr_cols = set(current.get("columns", []))

    changed_columns = []
    for col in sorted(prev_cols & curr_cols):
        prev_field = prev_dictionary.get(col, {})
        curr_field = curr_dictionary.get(col, {})
        deltas = {}
        if prev_field.get("non_null_pct") != curr_field.get("non_null_pct"):
            deltas["non_null_pct"] = {"before": prev_field.get("non_null_pct"), "after": curr_field.get("non_null_pct")}
        if prev_field.get("unique_count") != curr_field.get("unique_count"):
            deltas["unique_count"] = {"before": prev_field.get("unique_count"), "after": curr_field.get("unique_count")}
        if prev_field.get("category") != curr_field.get("category"):
            deltas["category"] = {"before": prev_field.get("category"), "after": curr_field.get("category")}
        if deltas:
            changed_columns.append({"column": col, "changes": deltas})

    return {
        "schema_changed": previous.get("schema_fingerprint") != current.get("schema_fingerprint"),
        "columns_added": sorted(curr_cols - prev_cols),
        "columns_removed": sorted(prev_cols - curr_cols),
        "row_count_delta": current.get("row_count", 0) - previous.get("row_count", 0),
        "changed_columns": changed_columns,
    }


# --------------------------------------------------------------------- #
# Cross-file relationship discovery
# --------------------------------------------------------------------- #

_MAX_ROWS_FOR_VALUE_OVERLAP = 200_000  # guard against O(n) set-building on huge uploads


def discover_shared_key_columns(file_rows: dict[str, list[dict]], min_overlap: float = 0.5) -> list[dict]:
    """For every pair of uploaded files, find column-name pairs (matched
    case-insensitively, ignoring underscores -- "participant_id" ~
    "ParticipantID") whose VALUES also substantially overlap, suggesting
    they're the same real-world key across files.

    `min_overlap` is the fraction of the smaller file's distinct values
    that must appear in the larger file's column for it to be reported.
    This never claims two files SHOULD be joined -- only that a column
    pair looks like a plausible shared key, for a human to confirm.
    """
    def normalize(name: str) -> str:
        return re.sub(r"[_\s]+", "", name).casefold()

    candidates = []
    filenames = list(file_rows.keys())
    for i, file_a in enumerate(filenames):
        for file_b in filenames[i + 1:]:
            rows_a, rows_b = file_rows[file_a], file_rows[file_b]
            if not rows_a or not rows_b:
                continue
            cols_a, cols_b = rows_a[0].keys(), rows_b[0].keys()
            for col_a in cols_a:
                for col_b in cols_b:
                    if normalize(col_a) != normalize(col_b):
                        continue
                    values_a = {str(r[col_a]) for r in rows_a[:_MAX_ROWS_FOR_VALUE_OVERLAP] if r.get(col_a) not in (None, "")}
                    values_b = {str(r[col_b]) for r in rows_b[:_MAX_ROWS_FOR_VALUE_OVERLAP] if r.get(col_b) not in (None, "")}
                    if not values_a or not values_b:
                        continue
                    smaller, larger = (values_a, values_b) if len(values_a) <= len(values_b) else (values_b, values_a)
                    overlap = len(smaller & larger) / len(smaller)
                    if overlap >= min_overlap:
                        candidates.append(
                            {
                                "file_a": file_a, "column_a": col_a,
                                "file_b": file_b, "column_b": col_b,
                                "overlap_fraction": round(overlap, 4),
                            }
                        )
    return sorted(candidates, key=lambda c: c["overlap_fraction"], reverse=True)


def check_referential_integrity(parent_values: set, child_values: set) -> dict:
    """How many values in `child_values` don't appear in `parent_values` --
    e.g. lab records referencing a participant_id absent from participants.csv.

    Deliberately doesn't conclude this IS a problem (a study design can
    legitimately have unmatched records); it just counts and samples them
    for a human to judge.
    """
    orphans = sorted(v for v in child_values if v not in parent_values)
    return {
        "child_count": len(child_values),
        "orphan_count": len(orphans),
        "orphan_examples": orphans[:10],
    }
