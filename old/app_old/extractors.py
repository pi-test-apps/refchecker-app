import re
from typing import Dict, List, Optional

__all__ = ["extract_apa_citations", "extract_reference_entries"]

_AUTHOR_CLEAN_RE = re.compile(r"[^a-z0-9]+")
_YEAR_RE = re.compile(r"\(\s*(?P<year>\d{4}[a-z]?)\s*\)")


def _normalize_author(author: str) -> str:
    cleaned = _AUTHOR_CLEAN_RE.sub("_", author.lower())
    return re.sub(r"_+", "_", cleaned).strip("_")


def _build_key(authors: str, year: str) -> str:
    year_norm = year.lower()
    authors_lower = authors.lower()
    if "et al" in authors_lower:
        first = authors.split()[0]
        author_part = f"{_normalize_author(first)}_et_al"
    elif "&" in authors:
        parts = [_normalize_author(part.strip()) for part in authors.split("&")]
        parts = [p for p in parts if p]
        author_part = "_".join(parts)
    else:
        author_part = _normalize_author(authors)

    return f"{author_part}_{year_norm}"


def extract_apa_citations(text: str) -> List[Dict[str, str]]:
    """Extract APA-style in-text citations and return unique keys with raw strings."""

    patterns = [
        # Parenthetical: (Surname, 2020) or (Surname & Surname, 2020) or (Surname et al., 2020a)
        re.compile(r"\((?P<authors>[A-Z][A-Za-z'\-\s&\.]+?),\s*(?P<year>\d{4}[a-z]?)\)"),
        # Narrative: Surname (2020) or Surname & Surname (2020) or Surname et al. (2020a)
        re.compile(
            r"\b(?P<authors>[A-Z][A-Za-z'\-]+(?:\s+et al\.?|(?:\s*&\s*|\s+)[A-Z][A-Za-z'\-]+)?)\s*\(\s*(?P<year>\d{4}[a-z]?)\s*\)"
        ),
    ]

    seen = set()
    results: List[Dict[str, str]] = []

    for pattern in patterns:
        for match in pattern.finditer(text):
            authors = match.group("authors").strip()
            year = match.group("year").strip()
            key = _build_key(authors, year)
            if key in seen:
                continue
            seen.add(key)
            results.append({"key": key, "raw": match.group(0)})

    return results


def _reference_key(entry: str) -> Optional[str]:
    """Try to build a key from first author surname and year."""

    author_match = re.match(r"\s*(?P<author>[A-Za-z'\-]+)", entry)
    year_match = _YEAR_RE.search(entry)
    if not author_match or not year_match:
        return None

    author = _normalize_author(author_match.group("author"))
    year = year_match.group("year").lower()
    return f"{author}_{year}"


_ENTRY_BOUNDARY_RE = re.compile(r"^[A-Z].*\(\d{4}[a-z]?\)")


def extract_reference_entries(text: str) -> List[Dict[str, str]]:
    """Extract reference entries after the final reference heading."""

    lines = text.splitlines()
    heading_indices = [
        idx
        for idx, line in enumerate(lines)
        if line.strip().lower() in {"references", "reference list"}
    ]
    if not heading_indices:
        return []

    start = heading_indices[-1] + 1
    refs_lines = lines[start:]

    entries: List[str] = []
    current: List[str] = []
    boundary_re = _ENTRY_BOUNDARY_RE

    for line in refs_lines:
        if not line.strip():
            if current:
                entries.append(" ".join(current).strip())
                current = []
            continue

        if boundary_re.match(line) and current:
            entries.append(" ".join(current).strip())
            current = [line.strip()]
        else:
            current.append(line.strip())

    if current:
        entries.append(" ".join(current).strip())

    results: List[Dict[str, str]] = []
    seen_entries = set()
    for entry in entries:
        if not entry or entry in seen_entries:
            continue
        seen_entries.add(entry)
        results.append({"entry": entry, "key": _reference_key(entry) or ""})

    return results
