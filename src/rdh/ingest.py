from pathlib import Path

from charset_normalizer import from_path

_CANDIDATE_DELIMITERS = [",", "\t", ";", "|"]
_FOOTER_MISMATCH_RUN = 2


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


def strip_footer(lines: list[str], delimiter: str) -> tuple[list[str], list[str]]:
    if not lines:
        return [], []

    header_fields = lines[0].count(delimiter) + 1
    mismatch_start = None
    run_length = 0

    for i in range(1, len(lines)):
        fields = lines[i].count(delimiter) + 1
        if fields != header_fields:
            run_length += 1
            if run_length >= _FOOTER_MISMATCH_RUN:
                mismatch_start = i - _FOOTER_MISMATCH_RUN + 1
                break
        else:
            run_length = 0

    if mismatch_start is None:
        return lines, []
    return lines[:mismatch_start], lines[mismatch_start:]
