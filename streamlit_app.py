from io import BytesIO
import json
from pathlib import Path

import streamlit as st
from docx import Document

from app.extractors import extract_apa_citations, extract_reference_entries
from app.matcher import build_report

import sys
import os

# Add the src directory to Python path so refchecker package can be found
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from refchecker.core.refchecker import main


ALLOWED_SUFFIXES = {".txt", ".docx", ".pdf"}


def _extract_text(uploaded_file) -> str:
    if not uploaded_file or not uploaded_file.name:
        raise ValueError("No file uploaded.")

    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Unsupported file type. Please upload a .txt, .pdf or .docx file.")

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
    st.title("Academic Paper Reference Checker")
    st.write("Upload a .txt, .pdf or .docx manuscript. We'll verify the accuracy of references by comparing cited information against authoritative sources")

    uploaded = st.file_uploader("Choose a .txt, .pdf or .docx file", type=["txt", "pdf", "docx"])

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
        cols = st.columns(5)
        cols[0].metric("Total references", report["total_references"])
        cols[1].metric("Errors", len(report["errors"]), delta=None)
        cols[2].metric("Warnings", len(report["warnings"]), delta=None)
        cols[3].metric("Improvings", len(report["improvings"]), delta=None)
        cols[4].metric("Not verified", len(report["missing"]), delta=None)

        st.subheader("Errors")
        if report["errors"]:
            st.write(report["errors"])
        else:
            st.write("No errors detected.")
            
        st.subheader("Warnings in reference list")
        if report["warnings"]:
            st.write(report["warnings"])
        else:
            st.write("No warnings.")
            
        st.subheader("Improvings")
        if report["improvings"]:
            st.write(report["improvings"])
        else:
            st.write("No improvings.")
            
        st.subheader("Not verified")
        if report["missing"]:
            st.write(report["missing"])
        else:
            st.write("All references were verified.")

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
