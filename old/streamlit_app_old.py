from io import BytesIO
import json
from pathlib import Path

import streamlit as st
from docx import Document

from app.extractors import extract_apa_citations, extract_reference_entries
from app.matcher import build_report

ALLOWED_SUFFIXES = {".txt", ".docx"}


def _extract_text(uploaded_file) -> str:
    if not uploaded_file or not uploaded_file.name:
        raise ValueError("No file uploaded.")

    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Unsupported file type. Please upload a .txt or .docx file.")

    content = uploaded_file.read()
    if suffix == ".txt":
        return content.decode("utf-8", errors="ignore")

    document = Document(BytesIO(content))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _build_report(text: str) -> dict:
    citations = extract_apa_citations(text)
    references = extract_reference_entries(text)
    if not references:
        raise ValueError("No References section found. Add a 'References' heading and at least one entry.")

    citation_keys = [c["key"] for c in citations]
    reference_keys = [r["key"] or r["entry"] for r in references if r.get("key") or r.get("entry")]
    report = build_report(citation_keys, reference_keys)
    report["citations"] = citations
    report["references"] = references
    report["char_count"] = len(text)
    report["preview"] = text[:500]
    return report


def main() -> None:
    st.set_page_config(page_title="Reference Checker", page_icon="📑", layout="wide")
    st.title("Reference Checker (Streamlit)")
    st.write("Upload a .txt or .docx manuscript. We'll extract in-text citations, parse the reference list, and highlight gaps.")

    uploaded = st.file_uploader("Choose a .txt or .docx file", type=["txt", "docx"])

    if uploaded and st.button("Run check"):
        try:
            text = _extract_text(uploaded)
            report = _build_report(text)
            st.session_state["report"] = report
            st.success("Analysis complete.")
        except Exception as exc:
            st.error(str(exc))
            return

    report = st.session_state.get("report")
    if report:
        cols = st.columns(4)
        cols[0].metric("Total citations", report["total_citations"])
        cols[1].metric("Total references", report["total_references"])
        cols[2].metric("Missing in reference list", len(report["missing_in_references"]), delta=None)
        cols[3].metric("Unused in text", len(report["unused_in_text"]), delta=None)

        st.subheader("Missing in reference list")
        if report["missing_in_references"]:
            st.write(report["missing_in_references"])
        else:
            st.write("All citations have matching references.")

        st.subheader("Unused in text")
        if report["unused_in_text"]:
            st.write(report["unused_in_text"])
        else:
            st.write("No unused references.")

        st.subheader("Preview (first 500 chars of text)")
        st.code(report.get("preview", ""), language="text")

        st.download_button(
            "Download report JSON",
            data=json.dumps(report, indent=2),
            file_name="report.json",
            mime="application/json",
        )


if __name__ == "__main__":
    main()
