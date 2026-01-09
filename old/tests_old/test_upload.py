from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_rejects_missing_file():
    response = client.post("/upload")
    assert response.status_code == 400
    assert response.json()["detail"] == "No file uploaded."


def test_rejects_unsupported_type():
    response = client.post(
        "/upload",
        files={"file": ("example.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_accepts_txt_file():
    payload = b"Hello references!"
    response = client.post(
        "/upload",
        files={"file": ("sample.txt", payload, "text/plain")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "sample.txt"
    assert data["char_count"] == len(payload.decode("utf-8"))
    assert data["text_preview"].startswith("Hello")


def test_accepts_docx_file():
    buffer = BytesIO()
    document = Document()
    document.add_paragraph("Docx content example.")
    document.save(buffer)
    buffer.seek(0)

    response = client.post(
        "/upload",
        files={
            "file": (
                "sample.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "sample.docx"
    assert "Docx content example." in data["text_preview"]
