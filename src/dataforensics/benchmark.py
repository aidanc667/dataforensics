"""Error-injection benchmark: measures what the checks actually catch.

1. Generate a clean synthetic dataset (seeded, so results are repeatable).
2. Plant known errors at known cells: missing-value codes, duplicate rows,
   whitespace, hidden characters, broken encoding, inconsistent category
   spellings, formatted numbers, mixed units, future dates, outliers, and
   missingness tied to age.
3. Run the full investigation on both files. On the dirty file, measure
   per-check recall (planted errors flagged / planted). On the clean
   file, count anything flagged at all (false alarms).
4. Approve every suggested fix, apply, and re-run, to measure how much of
   what was planted the cleaning actually removes.

The ground truth comes from the injector, never from the detectors, so a
detector can't grade itself.
"""

import csv
import random
import tempfile
from datetime import date, timedelta
from pathlib import Path

from dataforensics.cleaning import clean_dataset, unit_fix_params
from dataforensics.investigate import (
    find_encoding_corruption_evidence,
    find_future_date_evidence,
    find_invisible_character_evidence,
    find_numeric_representation_evidence,
    find_sentinel_evidence,
    find_whitespace_anomaly_evidence,
)
from dataforensics.pipeline import run_investigation

_STATES = ["CO", "WY", "UT", "NM", "AZ", "NE", "KS"]
_FIRST = ["Maria", "James", "Linda", "Wei", "Aisha", "Carlos", "Priya", "Tom", "Sofia", "Omar"]
_NOTES = ["routine intake", "follow-up", "no complaints", "referred by clinic", "seen by José", "annual visit"]


def generate_clean_rows(n: int, seed: int) -> list[dict]:
    """A plausible, clean intake table: every value well-formed, no codes,
    no duplicates, one unit per column, nothing missing."""
    rng = random.Random(seed)
    rows = []
    start = date(2023, 1, 1)
    for i in range(n):
        age = rng.randint(18, 89)
        rows.append(
            {
                "participant_id": f"P{i + 1:05d}",
                "first_name": rng.choice(_FIRST),
                "age": str(age),
                "sex": rng.choice(["Female", "Male"]),
                "state": rng.choice(_STATES),
                "income": str(rng.randint(20_000, 150_000)),
                "weight_kg": f"{rng.uniform(50, 110):.1f}",
                "visit_date": (start + timedelta(days=rng.randint(0, 364))).isoformat(),
                "notes": rng.choice(_NOTES),
            }
        )
    return rows


def inject_errors(clean: list[dict], seed: int) -> tuple[list[dict], dict[str, set]]:
    """Copy `clean` and plant errors. Returns (dirty rows, truth), where
    truth maps a check key to the set of planted items: (row_index,
    column) cells for cell-level checks, row indices for duplicates, and
    column names / (column, group) pairs for column-level checks. Each
    cell receives at most one planted error."""
    rng = random.Random(seed)
    rows = [dict(r) for r in clean]
    n = len(rows)
    used: set[tuple[int, str]] = set()
    truth: dict[str, set] = {}

    def pick(column: str, k: int, eligible=None) -> list[int]:
        pool = [i for i in range(n) if (i, column) not in used and (eligible is None or eligible(i))]
        chosen = rng.sample(pool, k)
        used.update((i, column) for i in chosen)
        return chosen

    # Missingness tied to age: income blank for most people 65+.
    older = [i for i in range(n) if int(rows[i]["age"]) >= 65]
    for i in rng.sample(older, int(len(older) * 0.6)):
        rows[i]["income"] = ""
        used.add((i, "income"))
    truth["group_missingness"] = {("income", "age")}

    for i in pick("income", 15, lambda i: rows[i]["income"] != ""):
        rows[i]["income"] = "9999"
        truth.setdefault("missing_value_codes", set()).add((i, "income"))
    for i in pick("income", 8, lambda i: rows[i]["income"] != ""):
        rows[i]["income"] = f"${int(rows[i]['income']):,}"
        truth.setdefault("numeric_representation", set()).add((i, "income"))
    for i in pick("state", 10):
        rows[i]["state"] = f" {rows[i]['state']} "
        truth.setdefault("whitespace", set()).add((i, "state"))
    for i in pick("first_name", 5):
        rows[i]["first_name"] = rows[i]["first_name"][:2] + "​" + rows[i]["first_name"][2:]
        truth.setdefault("invisible_characters", set()).add((i, "first_name"))
    for i in pick("notes", 5, lambda i: rows[i]["notes"] == "seen by José"):
        rows[i]["notes"] = "seen by JosÃ©"
        truth.setdefault("encoding_corruption", set()).add((i, "notes"))
    for i in pick("sex", 10):
        rows[i]["sex"] = rows[i]["sex"].lower()
        truth.setdefault("inconsistent_categories", set()).add((i, "sex"))
    for i in pick("weight_kg", max(6, n // 20)):
        rows[i]["weight_kg"] = f"{float(rows[i]['weight_kg']) * 2.20462:.1f}"
    truth["mixed_units"] = {"weight_kg"}
    for i in pick("visit_date", 5):
        rows[i]["visit_date"] = f"2099-0{rng.randint(1, 9)}-15"
        truth.setdefault("future_dates", set()).add((i, "visit_date"))
    for i in pick("age", 5, lambda i: int(rows[i]["age"]) < 65):
        rows[i]["age"] = str(rng.randint(300, 400))
    truth["statistical_outliers"] = {"age"}

    # Exact duplicates go last, copying already-dirty rows, so they're
    # identical to their source in every field.
    sources = rng.sample(range(n), 5)
    for k, src in enumerate(sources):
        rows.append(dict(rows[src]))
        truth.setdefault("duplicate_rows", set()).add(n + k)
        # The copy carries its source's planted cell errors too.
        for key, items in truth.items():
            items.update((n + k, col) for item in list(items) if isinstance(item, tuple) and item[0] == src for col in [item[1]])
    return rows, truth


def _write_csv(rows: list[dict], directory: Path, name: str) -> Path:
    path = directory / name
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _investigate(rows: list[dict], directory: Path, name: str) -> dict:
    path = _write_csv(rows, directory, name)
    return run_investigation(path, sheet=None, dataset_type="General", data_size=path.stat().st_size)


def _flagged_items(result: dict) -> dict[str, set]:
    """What each benchmarked check flagged, in the same units as truth."""
    rows = result["rows"]
    cells: dict[str, set] = {}

    def add(key: str, column: str, evidence) -> None:
        cells.setdefault(key, set()).update((i, column) for i, *_ in evidence)

    for col, vals in result["sentinels"].items():
        for v in vals:
            cells.setdefault("missing_value_codes", set()).update((i, col) for i in find_sentinel_evidence(rows, col, v))
    for col in result["numeric_representation_findings"]:
        add("numeric_representation", col, find_numeric_representation_evidence(rows, col))
    for col in result["whitespace_anomaly_counts"]:
        add("whitespace", col, find_whitespace_anomaly_evidence(rows, col))
    for col in result["invisible_char_counts"]:
        add("invisible_characters", col, find_invisible_character_evidence(rows, col))
    for col in result["encoding_corruption_counts"]:
        add("encoding_corruption", col, find_encoding_corruption_evidence(rows, col))
    for col, clusters in result["category_clusters"].items():
        for cluster in clusters:
            minority = [v for v in cluster["values"] if v != cluster["suggested_canonical"]]
            cells.setdefault("inconsistent_categories", set()).update(
                (i, col) for i, r in enumerate(rows) if r.get(col) in minority
            )
    for col in result["future_date_findings"]:
        add("future_dates", col, find_future_date_evidence(rows, col))
    cells["duplicate_rows"] = {d["row_index"] for d in result["dup_rows"]}
    cells["mixed_units"] = set(result["unit_inconsistency_findings"])
    cells["statistical_outliers"] = {
        c for c, f in result["dictionary"].items() if (f.get("outliers") or {}).get("outlier_count")
    }
    cells["group_missingness"] = {(g["column"], g["group_column"]) for g in result["group_missingness"]}
    return cells


_LABELS = {
    "missing_value_codes": "DF-04 Missing-value codes (9999)",
    "duplicate_rows": "DF-01 Exact duplicate rows",
    "whitespace": "DF-21 Whitespace",
    "invisible_characters": "DF-22 Hidden characters",
    "encoding_corruption": "DF-23 Broken encoding",
    "inconsistent_categories": "DF-08 Inconsistent categories",
    "numeric_representation": "DF-19 Numbers written as text",
    "mixed_units": "DF-17 Mixed units (column)",
    "future_dates": "DF-10 Future dates",
    "statistical_outliers": "DF-15 Outliers (column)",
    "group_missingness": "DF-32 Missingness tied to age (pair)",
}


def _approve_everything(result: dict) -> tuple[dict, dict]:
    """The rules an analyst would get by approving every suggestion."""
    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {col: {v: "" for v in vals} for col, vals in result["sentinels"].items()},
        "category_mappings": {
            col: {v: c["suggested_canonical"] for c in clusters for v in c["values"] if v != c["suggested_canonical"]}
            for col, clusters in result["category_clusters"].items()
        },
    }
    fixes: dict = {
        "whitespace": list(result["whitespace_anomaly_counts"]),
        "invisible": list(result["invisible_char_counts"]),
        "mojibake": list(result["encoding_corruption_counts"]),
        "numeric_format": list(result["numeric_representation_findings"]),
        # Overlapping unit clusters are flagged but never auto-converted.
        "units": {
            col: unit_fix_params(u) for col, u in result["unit_inconsistency_findings"].items() if u["separable"]
        },
    }
    return rules, fixes


def run_benchmark_seeds(n_rows: int = 1000, seeds: range = range(10)) -> dict:
    """run_benchmark over several seeds, summed per check -- one seed can
    get lucky or unlucky (e.g. kg and lb ranges overlapping)."""
    runs = [run_benchmark(n_rows, seed) for seed in seeds]
    totals: dict[str, dict] = {}
    for run in runs:
        for row in run["checks"]:
            t = totals.setdefault(row["Check"], {"Check": row["Check"], "Planted": 0, "Caught": 0, "Flags on clean items": 0, "Left after fixes": 0})
            for k in ("Planted", "Caught", "Flags on clean items", "Left after fixes"):
                t[k] += row[k]
    checks = [{**t, "Recall": round(t["Caught"] / t["Planted"], 3)} for t in totals.values()]
    return {
        "rows": n_rows,
        "seeds": len(runs),
        "checks": checks,
        "clean_false_alarm_runs": sum(1 for r in runs if r["clean_false_alarms"]),
        "clean_false_alarms": sorted({c for r in runs for c in r["clean_false_alarms"]}),
        "quality": {k: round(sum(r["quality"][k] for r in runs) / len(runs), 1) for k in ("clean", "dirty", "cleaned")},
    }


def run_benchmark(n_rows: int = 1000, seed: int = 0) -> dict:
    """Returns {"checks": [per-check rows], "clean_false_alarms": [check
    names flagged on the clean file], "quality": {clean, dirty, cleaned}}."""
    clean = generate_clean_rows(n_rows, seed)
    dirty, truth = inject_errors(clean, seed)
    with tempfile.TemporaryDirectory(prefix="dataforensics_bench_") as tmp:
        directory = Path(tmp)
        clean_result = _investigate(clean, directory, "clean.csv")
        dirty_result = _investigate(dirty, directory, "dirty.csv")

        rules, fixes = _approve_everything(dirty_result)
        fixes["drop_exact_duplicate_rows"] = True
        result = clean_dataset(dirty_result["rows"], rules, fixes, dirty_result["dup_rows"], ["participant_id"], reason="benchmark")
        if not result["safety"]["all_passed"]:
            raise RuntimeError(f"benchmark cleaning failed its safety check: {result['safety']}")
        cleaned = result["rows"]
        cleaned_result = _investigate(cleaned, directory, "cleaned.csv")

    flagged = _flagged_items(dirty_result)
    after = _flagged_items(cleaned_result)
    # A flag on a cell planted with a DIFFERENT error (e.g. " CO " is both
    # a whitespace error and a category variant) is a correct catch, not a
    # false alarm, so "extra" only counts flags on genuinely clean items.
    all_planted = set().union(*truth.values())
    checks = []
    for key, label in _LABELS.items():
        planted = truth[key]
        hits = flagged.get(key, set())
        caught = planted & hits
        checks.append(
            {
                "Check": label,
                "Planted": len(planted),
                "Caught": len(caught),
                "Recall": round(len(caught) / len(planted), 3),
                "Flags on clean items": len(hits - all_planted),
                "Left after fixes": len(after.get(key, set()) & planted),
            }
        )
    clean_false_alarms = [c["Check"] for c in clean_result["check_catalog"] if c["Status"] == "Flagged"]
    return {
        "rows": n_rows,
        "seed": seed,
        "checks": checks,
        "clean_false_alarms": clean_false_alarms,
        "quality": {
            "clean": clean_result["quality"]["overall_exact"],
            "dirty": dirty_result["quality"]["overall_exact"],
            "cleaned": cleaned_result["quality"]["overall_exact"],
        },
    }
