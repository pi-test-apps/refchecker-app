from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


SAMPLE_TEXT = """
Intro text with citation (Smith, 2020) and another Lopez (2019).

References
Smith, J. (2020). Article title. Journal Name.
Lopez, M. (2019). Another article. Journal Name.
"""


def test_check_generates_report_and_json():
    response = client.post(
        "/check",
        files={"file": ("sample.txt", SAMPLE_TEXT.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 200
    assert "Missing in reference list" in response.text
    json_response = client.get("/report.json")
    assert json_response.status_code == 200
    data = json_response.json()
    assert data["total_citations"] == 2
    assert data["total_references"] == 2
    assert data["missing_in_references"] == []
    assert data["unused_in_text"] == []


def test_missing_references_renders_error_page():
    text = "No heading here (Smith, 2020)."
    response = client.post(
        "/check",
        files={"file": ("no_refs.txt", text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 400
    assert "No References section found" in response.text


def test_rejects_unsupported_file_type():
    response = client.post(
        "/check",
        files={"file": ("bad.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.text
