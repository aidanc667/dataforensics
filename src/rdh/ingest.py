from pathlib import Path

from charset_normalizer import from_path

_CANDIDATE_DELIMITERS = [",", "\t", ";", "|"]


def detect_encoding(path: Path) -> str:
    result = from_path(str(path)).best()
    if result is None:
        return "utf-8"

    encoding = result.encoding
    # Normalize encoding name spelling to use hyphens instead of underscores
    # (e.g. "utf_8" -> "utf-8"). This only reformats the string charset_normalizer
    # returned; it never changes which encoding is being reported.
    encoding = encoding.replace("_", "-")

    return encoding


def detect_delimiter(sample_lines: list[str]) -> str:
    non_empty = [line for line in sample_lines if line.strip()]
    if not non_empty:
        return ","

    best_delim = ","
    best_score = -1
    for delim in _CANDIDATE_DELIMITERS:
        counts = [line.count(delim) for line in non_empty]
        if counts[0] == 0:
            continue
        consistent = sum(1 for c in counts if c == counts[0])
        score = consistent * counts[0]
        if score > best_score:
            best_score = score
            best_delim = delim
    return best_delim
