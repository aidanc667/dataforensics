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
from collections import Counter
from itertools import combinations

from dataforensics.typing_guards import parse_finite_float
from dataforensics.validation import is_ambiguous_date, is_date_like, is_iso_date

COMMON_SENTINEL_STRINGS = {
    # Bare "99" deliberately excluded: it's an extremely common
    # legitimate value in its own right (e.g. a neighborhood/district
    # code, a percentile, a real category id) far more often than it's
    # actually a missing-value convention, and _SENTINEL_DOMINANCE_THRESHOLD
    # alone wasn't enough -- a value that's rare in one dataset but a
    # real, correct answer in another still got flagged every time
    # regardless of frequency. "-99"/"999"/"9999" stay: a negative number
    # or an all-9s value of 3+ digits is a much stronger, more
    # unambiguous missing-value signal with far less legitimate-value
    # collision risk.
    "-99", "-9", "999", "9999",
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


# A missing-value convention is essentially never the dominant answer in
# real research/survey data -- documented non-response rates for even
# sensitive survey items rarely exceed ~20-30%. A sentinel-looking value
# that accounts for MORE than this share of a column's non-null values
# is far more likely a legitimate, common value that happens to match
# the pattern (e.g. "999" as a genuine numeric code, not a missing-value
# marker) than actual evidence of missingness -- so it's not flagged at
# all, rather than flagged with a misleadingly confident-sounding "looks
# like a common missing-value convention." Bare "99" hit this so often
# in practice that it's excluded from COMMON_SENTINEL_STRINGS entirely,
# above, rather than left to this threshold alone.
_SENTINEL_DOMINANCE_THRESHOLD = 0.25


def detect_candidate_sentinels(rows: list[dict], columns: list[str]) -> dict[str, list[str]]:
    """Values that look like common research/survey missing-value codes
    (e.g. "-99", "N/A", "Refused") appearing literally in the data --
    excluding any that account for more than _SENTINEL_DOMINANCE_THRESHOLD
    of the column's non-null values (see that constant's docstring).

    Never claims these ARE sentinels -- only that they match a common
    naming convention and are worth a human decision (map to a specific
    missing-value label, or leave as a legitimate value).
    """
    found: dict[str, list[str]] = {}
    for col in columns:
        raw_values = [str(row.get(col, "")).strip() for row in rows if row.get(col) not in (None, "")]
        if not raw_values:
            continue
        counts: dict[str, int] = {}
        for v in raw_values:
            counts[v] = counts.get(v, 0) + 1
        total = len(raw_values)
        hits = sorted(
            v
            for v in counts
            if v.casefold() in COMMON_SENTINEL_STRINGS and counts[v] / total <= _SENTINEL_DOMINANCE_THRESHOLD
        )
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


# A negation prefix can make two strings look like a near-identical typo
# pair by character-level similarity while meaning the exact opposite --
# rapidfuzz scores "employed"/"unemployed" at 88.9%, well above the
# default 85% clustering threshold, and a researcher who trusted the
# suggestion would silently merge two opposite survey categories into
# one. Checked on the ALREADY-normalized (trimmed/casefolded) strings, so
# it catches "Employed" vs "unemployed" too, not just an exact-case match.
_NEGATION_PREFIXES = ("un", "non", "in", "dis", "im", "ir", "il")


def _is_negation_pair(normalized_a: str, normalized_b: str) -> bool:
    shorter, longer = (normalized_a, normalized_b) if len(normalized_a) <= len(normalized_b) else (normalized_b, normalized_a)
    if not shorter:
        return False
    return any(longer == prefix + shorter for prefix in _NEGATION_PREFIXES)


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

    frequency = Counter(v for v in values if v and v.strip())
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
        elif fuzz.ratio(normalized[a], normalized[b]) >= threshold and not _is_negation_pair(normalized[a], normalized[b]):
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
        # Prefer the most FREQUENT member as the canonical suggestion --
        # the dominant spelling in the actual data is virtually always
        # the correct one, and this is what actually matters: a rare
        # OCR/typo corruption that happens to sort alphabetically first
        # (e.g. "158an Francisco", 1 occurrence, digit sorts before a
        # letter) must never outrank the overwhelmingly common correct
        # spelling ("San Francisco", tens of thousands of occurrences)
        # just because of where it falls in the alphabet. Ties (equal
        # frequency, e.g. every value in a small test fixture appearing
        # once) fall back to preferring an already-trimmed member -- a
        # plain alphabetical sort would pick a LEADING-whitespace variant
        # first (" Ada Lovelace" < "Ada Lovelace", since a space sorts
        # before a letter), exactly backwards, suggesting a merge INTO
        # the messier value instead of out of it -- and finally to plain
        # alphabetical order for full determinism.
        def _canonical_rank(v: str) -> tuple:
            return (-frequency[v], v != v.strip(), v)

        canonical = min(members, key=_canonical_rank)
        clusters.append(
            {
                "values": sorted(members),
                "suggested_canonical": canonical,
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
    # Checked before the generic DATE pattern below, so a birth-date-shaped
    # column name is never left to fall through to the generic role -- the
    # distinction matters for find_birth_date_after_other_date_evidence,
    # which needs to know specifically which date column is the birth date.
    ("DATE_OF_BIRTH", re.compile(r"(^|_)(dob|date_?of_?birth|birth_?date|birthdate)(_|$)", re.IGNORECASE)),
    ("DATE", re.compile(r"(^|_)(date|dt|visit_?date)(_|$)", re.IGNORECASE)),
    ("WEIGHT", re.compile(r"(^|_)weight(_kg|_lb|_lbs)?(_|$)", re.IGNORECASE)),
    ("HEIGHT", re.compile(r"(^|_)height(_cm|_in)?(_|$)", re.IGNORECASE)),
    # Checked before the generic INCOME pattern below, for the same reason
    # DATE_OF_BIRTH is checked before DATE: "income_to_poverty_ratio"
    # contains "income" as a substring but is a normalized RATIO (income
    # relative to a poverty threshold), not a dollar figure -- treating it
    # as the same role as a raw wage/earnings column is actively
    # misleading (a ratio and a currency amount aren't comparable or
    # interchangeable the way two dollar figures are), not just a loss of
    # detail.
    ("INCOME_TO_POVERTY_RATIO", re.compile(r"(^|_)poverty(_|$)", re.IGNORECASE)),
    ("INCOME", re.compile(r"(^|_)(income|salary|earnings)(_|$)", re.IGNORECASE)),
    ("RACE_OR_ETHNICITY", re.compile(r"(^|_)(race|ethnicity)(_|$)", re.IGNORECASE)),
    ("NAME", re.compile(r"(^|_)(name|full_?name|patient_?name|participant_?name)(_|$)", re.IGNORECASE)),
    ("ZIP_OR_POSTAL", re.compile(r"(^|_)(zip|zip_?code|postal_?code)(_|$)", re.IGNORECASE)),
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


def _is_numeric_value(value: str) -> bool:
    return parse_finite_float(value) is not None


def classify_column_types(dictionary: dict, rows: list[dict]) -> dict[str, str]:
    """One of "identifier" / "numeric" / "date" / "categorical" /
    "mixed_uncertain" per column -- a coarser, presentation-oriented
    classification for a "what did I receive?" investigation overview.
    This does NOT replace dictionary.py's own "category" field (id /
    categorical / free_text), which every other check in this codebase
    already depends on; it's a separate, additive read of the same data
    for a different, simpler purpose.

    Priority per column: identifier (dictionary.py already classified it
    "id") > date (every non-null value is date-shaped) > numeric (every
    non-null value is float-parseable) > categorical (dictionary.py's own
    "categorical" classification) > mixed_uncertain (everything else,
    including a column with no non-null values to classify from).
    """
    result: dict[str, str] = {}
    for col, entry in dictionary.items():
        if entry.get("category") == "id":
            result[col] = "identifier"
            continue
        non_null = [str(row.get(col, "")).strip() for row in rows if row.get(col, "") != ""]
        if not non_null:
            result[col] = "mixed_uncertain"
        elif all(is_date_like(v) for v in non_null):
            result[col] = "date"
        elif all(_is_numeric_value(v) for v in non_null):
            result[col] = "numeric"
        elif entry.get("category") == "categorical":
            result[col] = "categorical"
        else:
            result[col] = "mixed_uncertain"
    return result


# --------------------------------------------------------------------- #
# Cross-column reasoning -- checks that look at the RELATIONSHIP between
# two or more columns in the same row, not just one column in isolation.
# Still pure detection: never concludes a relationship IS a problem
# (a birth date after a visit date is impossible; a shared identity
# across two IDs merely deserves a look), and never merges or modifies
# anything.
# --------------------------------------------------------------------- #


def find_birth_date_after_other_date_evidence(
    rows: list[dict], birth_date_column: str, other_date_column: str
) -> list[tuple[int, str, str]]:
    """Real (row_index, birth_date_value, other_date_value) triples where
    `birth_date_column`'s value is chronologically AFTER
    `other_date_column`'s value in the same row -- not possible for the
    same real-world person (nobody's birth date follows another event in
    their own record).

    Only compares values where BOTH are unambiguous ISO dates
    (YYYY-MM-DD, via is_iso_date) -- an ambiguous (MM/DD vs DD/MM) or
    otherwise unparseable value on either side is skipped rather than
    guessed at, since comparing two non-ISO shapes as plain strings would
    silently produce a meaningless ordering.
    """
    evidence = []
    for i, row in enumerate(rows):
        birth_raw = str(row.get(birth_date_column, "")).strip()
        other_raw = str(row.get(other_date_column, "")).strip()
        if not (is_iso_date(birth_raw) and is_iso_date(other_raw)):
            continue
        if birth_raw > other_raw:
            evidence.append((i, birth_raw, other_raw))
    return evidence


def detect_duplicate_entities(rows: list[dict], quasi_identifier_columns: list[str], id_column: str) -> list[dict]:
    """Groups of rows that share the same value (case/whitespace-
    normalized) across EVERY column in `quasi_identifier_columns`, but
    have 2+ DISTINCT values in `id_column` -- e.g. the same name, date of
    birth, and ZIP code recorded under two different participant IDs.

    Distinct from detect_conflicting_id_records (same ID, different
    other fields): here the ID itself differs but everything else lines
    up, suggesting the same real-world entity may have been assigned two
    IDs. A row missing any one of the quasi-identifier values is skipped
    entirely -- an incomplete tuple isn't meaningful evidence either way.
    Never concludes these ARE the same entity, and never merges anything
    -- only that a human should look.

    Returns [] immediately if `quasi_identifier_columns` is empty --
    grouping rows on zero columns groups every row into one match by
    construction, which is not evidence of anything and would otherwise
    silently flag the entire dataset as one giant "duplicate."
    """
    if not quasi_identifier_columns:
        return []

    groups: dict[tuple, list[int]] = {}
    for i, row in enumerate(rows):
        key_values = [row.get(c) for c in quasi_identifier_columns]
        if any(v in (None, "") for v in key_values):
            continue
        key = tuple(str(v).strip().casefold() for v in key_values)
        groups.setdefault(key, []).append(i)

    duplicates = []
    for indices in groups.values():
        if len(indices) < 2:
            continue
        id_values = {rows[i].get(id_column) for i in indices}
        if len(id_values) >= 2:
            duplicates.append({"row_indices": indices, "id_values": sorted(str(v) for v in id_values)})
    return duplicates


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


# --------------------------------------------------------------------- #
# Dataset-type profiles — optional, additional checks a human opts into
# by declaring what kind of dataset this is (Survey / Clinical / Research,
# Geographic). "General" runs none of these; every check below is purely
# additive to the always-on checks above, still detection-only, and still
# requires human review before anything is approved. Deliberately narrow:
# a handful of well-known, name-pattern-triggered, defensible rules rather
# than an attempt to guess every possible domain-specific issue.
# --------------------------------------------------------------------- #

# Widely-accepted plausible-range bounds for a small set of common clinical
# / research measurements, matched against a column by name pattern. Every
# bound here is stated explicitly in the evidence panel shown for a flagged
# value, so a human can judge the rule itself, not just trust it.
CLINICAL_RANGE_RULES: list[dict] = [
    {"pattern": re.compile(r"(^|_)age(_|$)", re.IGNORECASE), "min": 0, "max": 120, "label": "age", "unit": "years"},
    {"pattern": re.compile(r"(^|_)height_?cm(_|$)", re.IGNORECASE), "min": 30, "max": 250, "label": "height", "unit": "cm"},
    {"pattern": re.compile(r"(^|_)weight_?kg(_|$)", re.IGNORECASE), "min": 1, "max": 300, "label": "weight", "unit": "kg"},
    {"pattern": re.compile(r"(^|_)bmi(_|$)", re.IGNORECASE), "min": 10, "max": 80, "label": "BMI", "unit": ""},
]

_SURVEY_WEIGHT_PATTERN = re.compile(r"(^|_)(wt|wgt|weight)(_|\d|$)", re.IGNORECASE)

# FIPS: 2-digit (state) or 5-digit (state+county) numeric string. Leading
# zeros matter (Alabama is "01"), so this matches the raw string, never a
# parsed integer.
_FIPS_PATTERN = re.compile(r"^\d{2}(\d{3})?$")
# ZIP: 5-digit, or ZIP+4 (5 digits, hyphen, 4 digits).
_ZIP_PATTERN = re.compile(r"^\d{5}(-\d{4})?$")

_FIPS_COLUMN_NAME_PATTERN = re.compile(r"fips", re.IGNORECASE)
_ZIP_COLUMN_NAME_PATTERN = re.compile(r"zip", re.IGNORECASE)


def find_fips_like_columns(columns: list[str]) -> list[str]:
    """Column names containing "fips" (case-insensitive) -- candidates for
    find_invalid_fips_evidence under the Geographic dataset profile."""
    return [c for c in columns if _FIPS_COLUMN_NAME_PATTERN.search(c)]


def find_zip_like_columns(columns: list[str]) -> list[str]:
    """Column names containing "zip" (case-insensitive) -- candidates for
    find_invalid_zip_evidence under the Geographic dataset profile."""
    return [c for c in columns if _ZIP_COLUMN_NAME_PATTERN.search(c)]

DATASET_PROFILES = {
    "General": [],
    "Survey": ["survey_weight_columns"],
    "Clinical / Research": ["clinical_ranges", "conflicting_id_records"],
    "Geographic": ["fips_format", "zip_format"],
}


def match_clinical_range_rule(column: str) -> dict | None:
    """Return the first CLINICAL_RANGE_RULES entry whose name pattern
    matches `column`, or None if no rule applies to this column."""
    for rule in CLINICAL_RANGE_RULES:
        if rule["pattern"].search(column):
            return rule
    return None


def find_implausible_value_evidence(
    rows: list[dict], column: str, min_value: float, max_value: float
) -> list[tuple[int, str]]:
    """Real (row_index, raw_value) pairs for numeric-parseable values in
    `column` outside [min_value, max_value]. Non-numeric values are
    skipped, not flagged -- this is a range check, not a type check."""
    evidence = []
    for i, row in enumerate(rows):
        raw = row.get(column, "")
        if raw == "":
            continue
        numeric = parse_finite_float(raw)
        if numeric is None:
            continue
        if numeric < min_value or numeric > max_value:
            evidence.append((i, raw))
    return evidence


def detect_conflicting_id_records(rows: list[dict], id_column: str) -> list[dict]:
    """Values of `id_column` appearing on 2+ rows where at least one OTHER
    field differs between those rows -- e.g. the same participant_id with
    two different recorded ages. Distinct from detect_duplicate_rows,
    which only catches EXACT full-row repeats; this catches the more
    common and more concerning case of the same real-world entity recorded
    inconsistently. Never concludes which row is correct -- only that a
    human should look.
    """
    by_id: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        key = row.get(id_column)
        if key in (None, ""):
            continue
        by_id.setdefault(str(key), []).append(i)

    conflicts = []
    for id_value, indices in by_id.items():
        if len(indices) < 2:
            continue
        first = rows[indices[0]]
        if any(rows[i] != first for i in indices[1:]):
            conflicts.append({"id_value": id_value, "row_indices": indices})
    return conflicts


def find_invalid_fips_evidence(rows: list[dict], column: str) -> list[tuple[int, str]]:
    """Real (row_index, raw_value) pairs for non-null values in `column`
    that don't match a 2-digit (state) or 5-digit (state+county) numeric
    FIPS code -- the standard US Census geographic identifier shape."""
    evidence = []
    for i, row in enumerate(rows):
        raw = str(row.get(column, "")).strip()
        if raw == "":
            continue
        if not _FIPS_PATTERN.match(raw):
            evidence.append((i, raw))
    return evidence


def find_invalid_zip_evidence(rows: list[dict], column: str) -> list[tuple[int, str]]:
    """Real (row_index, raw_value) pairs for non-null values in `column`
    that don't match a 5-digit ZIP or ZIP+4 shape."""
    evidence = []
    for i, row in enumerate(rows):
        raw = str(row.get(column, "")).strip()
        if raw == "":
            continue
        if not _ZIP_PATTERN.match(raw):
            evidence.append((i, raw))
    return evidence


_SHAPE_MIN_VALUES = 5
_SHAPE_DOMINANT_MIN_FRACTION = 0.8


def _value_shape(value: str) -> str:
    """Reduce `value` to a run-length-encoded character-class signature --
    digits collapse to "D", letters to "L", whitespace to "S", and any other
    character (punctuation/symbols) is kept literally, with consecutive
    runs of the same class collapsed to one symbol.

    This is a general, type-agnostic stand-in for "does this value have the
    same format as the rest of the column" that doesn't require knowing in
    advance whether the column holds phone numbers, emails, names, or
    something else: "555-123-4567" and "555-987-6543" both reduce to
    "D-D-D"; "(555) 123-4567" reduces to the different shape "(D)SD-D";
    "john@example.com" reduces to "L@L.L"; "John Smith" reduces to "LSL".
    """
    shape: list[str] = []
    for ch in value:
        if ch.isdigit():
            cls = "D"
        elif ch.isalpha():
            cls = "L"
        elif ch.isspace():
            cls = "S"
        else:
            cls = ch
        if not shape or shape[-1] != cls:
            shape.append(cls)
    return "".join(shape)


def detect_value_shape_outliers(rows: list[dict], columns: list[str], dictionary: dict) -> dict[str, dict]:
    """Flag free_text columns where one value shape (see `_value_shape`)
    dominates (>= 80% of non-null values) and a minority of values don't
    match it -- a general format-consistency check that works for any
    column (names, phone numbers, emails, ...) rather than the specific
    types (ZIP, FIPS) this module already has dedicated checks for.

    Deliberately restricted to "free_text" columns (never "id" or
    "categorical" -- the same category this module already treats as
    "continuous/varied enough to reason about numerically" elsewhere), and
    only fires when a dominant shape actually exists. A genuine free-text
    column (notes, comments) has no consistent shape to begin with, so no
    shape will reach the 80% bar and the column is silently skipped --
    this is not evidence of anything, just nothing defensible to report.

    Returns {column: {"dominant_shape": str, "dominant_count": int,
    "total_count": int, "outlier_count": int}}.
    """
    results: dict[str, dict] = {}
    for column in columns:
        if dictionary.get(column, {}).get("category") != "free_text":
            continue
        values = [str(row.get(column, "")).strip() for row in rows]
        values = [v for v in values if v != ""]
        if len(values) < _SHAPE_MIN_VALUES:
            continue
        shape_counts = Counter(_value_shape(v) for v in values)
        dominant_shape, dominant_count = shape_counts.most_common(1)[0]
        total = len(values)
        if dominant_count / total < _SHAPE_DOMINANT_MIN_FRACTION:
            continue
        outlier_count = total - dominant_count
        if outlier_count == 0:
            continue
        results[column] = {
            "dominant_shape": dominant_shape,
            "dominant_count": dominant_count,
            "total_count": total,
            "outlier_count": outlier_count,
        }
    return results


def find_value_shape_outlier_evidence(rows: list[dict], column: str, dominant_shape: str) -> list[tuple[int, str]]:
    """Real (row_index, raw_value) pairs for non-null values in `column`
    whose value shape (see `_value_shape`) doesn't match `dominant_shape`."""
    evidence = []
    for i, row in enumerate(rows):
        raw = str(row.get(column, "")).strip()
        if raw == "":
            continue
        if _value_shape(raw) != dominant_shape:
            evidence.append((i, raw))
    return evidence


def detect_survey_weight_columns(columns: list[str]) -> list[str]:
    """Column names that look like a survey sampling/replicate weight
    (e.g. "wt_final", "wgt2011") -- purely a name-pattern flag, never
    inferred from the values themselves, since a sampling weight is a
    study-design concept no value-shape heuristic can confirm. Meant to
    warn against treating it as an ordinary numeric variable (e.g.
    flagging it for outliers) without realizing what it actually
    represents."""
    return [c for c in columns if _SURVEY_WEIGHT_PATTERN.search(c)]


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


def analyze_key_cardinality(child_values_all: list[str], parent_values: set) -> dict:
    """How many child ROWS reference each parent key -- e.g. participants
    -> visits is "one-to-many" if any participant_id appears on 2+ visit
    rows, "one-to-one" if every matched parent has at most one child row.

    Unlike check_referential_integrity (which operates on deduplicated
    sets and so can't see repetition), this takes the child column's full,
    non-deduplicated value list -- repetition IS the signal here. Orphan
    values (not in parent_values) are excluded, matching
    check_referential_integrity's scope: that's a separate concern.
    """
    counts = Counter(v for v in child_values_all if v in parent_values)
    if not counts:
        return {"relationship": "no_matches", "max_children_per_parent": 0, "parents_with_multiple_children": 0}
    max_count = max(counts.values())
    return {
        "relationship": "one_to_many" if max_count > 1 else "one_to_one",
        "max_children_per_parent": max_count,
        "parents_with_multiple_children": sum(1 for c in counts.values() if c > 1),
    }


def compute_key_coverage(parent_values: set, child_values: set) -> dict:
    """What fraction of parent_values have at least one matching record in
    child_values -- e.g. "94.7% of participants have at least one visit."
    The complement of orphan analysis: that asks whether every CHILD has a
    parent, this asks whether every PARENT has a child.
    """
    if not parent_values:
        return {"parent_count": 0, "covered_count": 0, "coverage_fraction": 0.0}
    covered_count = len(parent_values & child_values)
    return {
        "parent_count": len(parent_values),
        "covered_count": covered_count,
        "coverage_fraction": covered_count / len(parent_values),
    }


def build_missingness_overview(dictionary: dict) -> list[dict]:
    """Every column's missingness, not just the ones crossing a
    "substantial missingness" threshold -- the full picture a researcher
    needs to plan analysis-dataset construction, sorted worst-first."""
    return sorted(
        (
            {
                "column": col,
                "missing_pct": round(100.0 - info["non_null_pct"], 4),
                "non_null_pct": info["non_null_pct"],
            }
            for col, info in dictionary.items()
        ),
        key=lambda row: row["missing_pct"],
        reverse=True,
    )


_MISSINGNESS_PATTERN_MIN_GROUP = 5
_MISSINGNESS_CONCENTRATION_MIN_RELATIVE_GAP = 0.2
_MISSINGNESS_CO_OCCURRENCE_MIN_OVERLAP = 0.5


def _median(values: list[float]) -> float:
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


def detect_missingness_concentration(
    rows: list[dict], target_column: str, numeric_columns: list[str]
) -> list[dict]:
    """For target_column's missing values, compare each candidate numeric
    column's median between rows where target_column IS missing and rows
    where it's present -- e.g. a much higher median age in the
    BMI-missing group surfaces as "BMI missingness is concentrated in
    participants over age 75."

    This is a plain median comparison, not a hypothesis test -- it
    reports the two medians and both group sizes so a human can judge
    whether the gap is meaningful, rather than asserting statistical
    significance this simple a check can't actually establish. Requires
    at least _MISSINGNESS_PATTERN_MIN_GROUP rows on both sides (missing
    and present, and enough numeric-parseable values in each) before
    comparing anything -- a pattern computed from a handful of rows isn't
    evidence of anything.
    """
    missing_idx = [i for i, row in enumerate(rows) if row.get(target_column, "") == ""]
    present_idx = [i for i, row in enumerate(rows) if row.get(target_column, "") != ""]
    if len(missing_idx) < _MISSINGNESS_PATTERN_MIN_GROUP or len(present_idx) < _MISSINGNESS_PATTERN_MIN_GROUP:
        return []

    results = []
    for col in numeric_columns:
        if col == target_column:
            continue
        missing_vals = sorted(
            v for i in missing_idx if (v := parse_finite_float(rows[i].get(col, ""))) is not None
        )
        present_vals = sorted(
            v for i in present_idx if (v := parse_finite_float(rows[i].get(col, ""))) is not None
        )
        if len(missing_vals) < _MISSINGNESS_PATTERN_MIN_GROUP or len(present_vals) < _MISSINGNESS_PATTERN_MIN_GROUP:
            continue
        median_missing = _median(missing_vals)
        median_present = _median(present_vals)
        if median_present == 0:
            continue
        relative_gap = abs(median_missing - median_present) / abs(median_present)
        if relative_gap >= _MISSINGNESS_CONCENTRATION_MIN_RELATIVE_GAP:
            results.append(
                {
                    "column": col,
                    "median_when_missing": median_missing,
                    "median_when_present": median_present,
                    "missing_group_size": len(missing_vals),
                    "present_group_size": len(present_vals),
                    "relative_gap": relative_gap,
                    "direction": "higher" if median_missing > median_present else "lower",
                }
            )
    return sorted(results, key=lambda r: r["relative_gap"], reverse=True)


def detect_missingness_co_occurrence(rows: list[dict], columns: list[str]) -> list[dict]:
    """Column pairs whose missingness co-occurs far more than a handful of
    coincidental blanks would -- e.g. Income and Employment Status often
    blank on the same records, suggesting a shared cause (the same
    survey section skipped, the same non-response) rather than
    independent data entry gaps.

    Reports raw counts and the overlap fraction only -- like
    detect_missingness_concentration, this is not a statistical
    independence test, just evidence for a human to weigh. Both columns
    need at least _MISSINGNESS_PATTERN_MIN_GROUP missing rows before
    being considered.
    """
    missing_sets: dict[str, set[int]] = {}
    for col in columns:
        idx = {i for i, row in enumerate(rows) if row.get(col, "") == ""}
        if len(idx) >= _MISSINGNESS_PATTERN_MIN_GROUP:
            missing_sets[col] = idx

    results = []
    cols = list(missing_sets.keys())
    for i, col_a in enumerate(cols):
        for col_b in cols[i + 1 :]:
            both = missing_sets[col_a] & missing_sets[col_b]
            smaller = min(len(missing_sets[col_a]), len(missing_sets[col_b]))
            if smaller == 0 or len(both) < _MISSINGNESS_PATTERN_MIN_GROUP:
                continue
            overlap_fraction = len(both) / smaller
            if overlap_fraction >= _MISSINGNESS_CO_OCCURRENCE_MIN_OVERLAP:
                results.append(
                    {
                        "column_a": col_a,
                        "column_b": col_b,
                        "both_missing_count": len(both),
                        "column_a_missing_count": len(missing_sets[col_a]),
                        "column_b_missing_count": len(missing_sets[col_b]),
                        "overlap_fraction": overlap_fraction,
                    }
                )
    return sorted(results, key=lambda r: r["overlap_fraction"], reverse=True)
