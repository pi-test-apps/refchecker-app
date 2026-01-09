from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from docx import Document
from fastapi import File, FastAPI, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.extractors import extract_apa_citations, extract_reference_entries
from app.matcher import build_report

app = FastAPI(title="Reference Checker")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

ALLOWED_SUFFIXES = {".txt", ".docx"}
_last_report: dict[str, Any] | None = None


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


def _render_error(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "message": message},
        status_code=status_code,
    )


async def _extract_text(file: Optional[UploadFile]) -> str:
    if not file or not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file uploaded.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type. Please upload a .txt or .docx file.")

    try:
        content = await file.read()
    except Exception as exc:  # pragma: no cover - defensive guard
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not read uploaded file.") from exc

    if suffix == ".txt":
        text = content.decode("utf-8", errors="ignore")
    else:
        try:
            document = Document(BytesIO(content))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        except Exception as exc:  # pragma: no cover - defensive guard
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not process .docx file.") from exc

    return text


@app.post("/upload")
async def upload_file(file: Optional[UploadFile] = File(None)) -> dict[str, str | int]:
    text = await _extract_text(file)
    preview = text[:500]
    return {
        "filename": file.filename,
        "char_count": len(text),
        "text_preview": preview,
    }


@app.post("/check", response_class=HTMLResponse)
async def check(request: Request, file: Optional[UploadFile] = File(None)):
    global _last_report

    try:
        text = await _extract_text(file)
    except HTTPException as exc:
        return _render_error(request, str(exc.detail), status_code=exc.status_code)

    citations = extract_apa_citations(text)
    references = extract_reference_entries(text)
    if not references:
        return _render_error(
            request,
            "No References section found. Add a 'References' heading and at least one entry.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    citation_keys = [c["key"] for c in citations]
    reference_keys = [r["key"] or r["entry"] for r in references if r.get("key") or r.get("entry")]

    report = build_report(citation_keys, reference_keys)
    report["citations"] = citations
    report["references"] = references
    _last_report = report

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "report": report,
            "missing_count": len(report["missing_in_references"]),
            "unused_count": len(report["unused_in_text"]),
        },
    )


@app.get("/report.json")
async def report_json():
    if _last_report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No report generated yet.")
    return JSONResponse(_last_report)
