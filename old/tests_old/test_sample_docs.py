from pathlib import Path

from fastapi.testclient import TestClient

from app.extractors import extract_apa_citations, extract_reference_entries
from app.matcher import build_report
from app.main import app


client = TestClient(app)
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_docs"


def test_docx_end_to_end_detects_missing_and_unused():
    sample_docx = SAMPLE_DIR / "sample_apa.docx"
    response = client.post(
        "/check",
        files={"file": (sample_docx.name, sample_docx.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert response.status_code == 200
    html = response.text
    assert "Missing in reference list" in html
    data = client.get("/report.json").json()
    assert "taylor_2022" in data["missing_in_references"]
    assert "unused_2016" in data["unused_in_text"]


def test_extractors_on_sample_text():
    text = (SAMPLE_DIR / "sample_apa.txt").read_text(encoding="utf-8")
    citations = extract_apa_citations(text)
    references = extract_reference_entries(text)
    keys = {c["key"] for c in citations}
    ref_keys = {r["key"] for r in references}
    assert "taylor_2022" in keys
    assert "unused_2016" in ref_keys
    assert len(citations) >= 6
    assert len(references) >= 6


def test_matching_identifies_differences_on_sample():
    text = (SAMPLE_DIR / "sample_apa.txt").read_text(encoding="utf-8")
    citations = extract_apa_citations(text)
    references = extract_reference_entries(text)
    report = build_report([c["key"] for c in citations], [r["key"] for r in references])
    assert "taylor_2022" in report["missing_in_references"]
    assert "unused_2016" in report["unused_in_text"]
