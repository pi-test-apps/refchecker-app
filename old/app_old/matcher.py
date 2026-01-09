from typing import Any, Dict, Iterable, List

__all__ = ["build_report"]


def _unique(sequence: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in sequence:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def build_report(citations: List[str], references: List[str]) -> Dict[str, Any]:
    """Build a simple matching report between citation keys and reference keys."""

    citations_unique = _unique(citations)
    references_unique = _unique(references)

    citations_set = set(citations_unique)
    references_set = set(references_unique)

    missing_in_references = sorted(citations_set - references_set)
    unused_in_text = sorted(references_set - citations_set)

    return {
        "total_citations": len(citations),
        "total_references": len(references),
        "missing_in_references": missing_in_references,
        "unused_in_text": unused_in_text,
        "citation_examples": citations_unique[:5],
        "reference_examples": references_unique[:5],
    }
