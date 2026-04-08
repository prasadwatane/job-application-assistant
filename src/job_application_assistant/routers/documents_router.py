"""
Documents Router  —  /documents
==================================
Full CRUD + preview + edit for resumes and cover letters.

Resumes
  GET  /documents/resumes                  list all PDF resumes
  POST /documents/resumes/upload           upload a new resume PDF
  GET  /documents/resumes/{name}/text      extract text from a resume
  DELETE /documents/resumes/{name}         remove a resume

Cover Letters
  GET  /documents/cover-letters            list all generated cover letters
  GET  /documents/cover-letters/{name}/text   extract text from a cover letter PDF
  PUT  /documents/cover-letters/{name}        edit text + regenerate PDF
  DELETE /documents/cover-letters/{name}      remove a cover letter
  GET  /documents/cover-letters/{name}/download  download the PDF
"""

import os
import io
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter()
FILES_AUTH_KEY = os.getenv("FILES_AUTH_KEY", "changeme")


def _auth(key: str = Query(...)):
    if key != FILES_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid auth key.")


def _resume_dir() -> Path:
    try:
        with open("config.yml") as f:
            cfg = yaml.safe_load(f)
        return Path(cfg.get("resume_directory", "resumes"))
    except Exception:
        return Path("resumes")


def _cl_dir() -> Path:
    try:
        with open("config.yml") as f:
            cfg = yaml.safe_load(f)
        return Path(cfg.get("cover_letters_dir", "generated_cover_letters"))
    except Exception:
        return Path("generated_cover_letters")


def _extract_pdf_text(path: Path) -> str:
    """Extract all text from a PDF file."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                pages.append(f"[Page {i+1}]\n{text.strip()}")
        return "\n\n".join(pages) if pages else "(No text could be extracted from this PDF)"
    except Exception as e:
        return f"(Error extracting text: {e})"


def _regenerate_pdf(path: Path, new_text: str) -> None:
    """Overwrite a PDF with new text content."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_JUSTIFY
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib import colors
        import re

        doc = SimpleDocTemplate(
            str(path), pagesize=A4,
            leftMargin=2.5*cm, rightMargin=2.5*cm,
            topMargin=2.5*cm, bottomMargin=2.5*cm,
        )

        body_style = ParagraphStyle(
            "Body", fontName="Helvetica", fontSize=10.5,
            leading=16, alignment=TA_JUSTIFY,
            textColor=colors.HexColor("#1a1a1a"), spaceAfter=10,
        )

        def _escape(t):
            return t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

        def _process(line):
            line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _escape(line))
            return line

        story = []
        for line in new_text.split("\n"):
            stripped = line.strip()
            if not stripped:
                story.append(Spacer(1, 8))
            else:
                story.append(Paragraph(_process(stripped), body_style))

        doc.build(story)
    except ImportError:
        # Fallback: save as text if reportlab not available
        path.with_suffix(".txt").write_text(new_text, encoding="utf-8")
        raise RuntimeError("reportlab not installed — saved as .txt instead")


# ── Resumes ─────────────────────────────────────────────────────────────────

@router.get("/resumes")
def list_resumes(auth=Depends(_auth)):
    d = _resume_dir()
    d.mkdir(parents=True, exist_ok=True)
    files = sorted(d.glob("*.pdf"), key=lambda f: f.stat().st_mtime, reverse=True)
    return {
        "directory": str(d.resolve()),
        "count": len(files),
        "resumes": [
            {
                "filename": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": f.stat().st_mtime,
            }
            for f in files
        ],
    }


@router.post("/resumes/upload", status_code=201)
async def upload_resume(
    file: UploadFile = File(...),
    auth=Depends(_auth),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted.")
    d = _resume_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / file.filename
    content = await file.read()
    dest.write_bytes(content)
    return {
        "message": f"Resume '{file.filename}' uploaded.",
        "filename": file.filename,
        "size_kb": round(len(content) / 1024, 1),
    }


@router.get("/resumes/{filename}/text")
def resume_text(filename: str, auth=Depends(_auth)):
    path = _resume_dir() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Resume '{filename}' not found.")
    return {
        "filename": filename,
        "text": _extract_pdf_text(path),
    }


@router.delete("/resumes/{filename}")
def delete_resume(filename: str, auth=Depends(_auth)):
    path = _resume_dir() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Resume '{filename}' not found.")
    path.unlink()
    return {"message": f"Resume '{filename}' deleted."}


# ── Cover Letters ────────────────────────────────────────────────────────────

@router.get("/cover-letters")
def list_cover_letters(auth=Depends(_auth)):
    d = _cl_dir()
    d.mkdir(parents=True, exist_ok=True)
    files = sorted(
        list(d.glob("*.pdf")) + list(d.glob("*.txt")),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return {
        "directory": str(d.resolve()),
        "count": len(files),
        "cover_letters": [
            {
                "filename": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": f.stat().st_mtime,
                "type": f.suffix.lstrip("."),
            }
            for f in files
        ],
    }


@router.get("/cover-letters/{filename}/text")
def cover_letter_text(filename: str, auth=Depends(_auth)):
    path = _cl_dir() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Cover letter '{filename}' not found.")
    if path.suffix.lower() == ".txt":
        text = path.read_text(encoding="utf-8")
    else:
        text = _extract_pdf_text(path)
    return {"filename": filename, "text": text}


class EditCoverLetterRequest(BaseModel):
    text: str


@router.put("/cover-letters/{filename}")
def edit_cover_letter(filename: str, body: EditCoverLetterRequest, auth=Depends(_auth)):
    """
    Update a cover letter with new text and regenerate the PDF.
    Also saves a .txt backup alongside the PDF.
    """
    d = _cl_dir()
    path = d / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Cover letter '{filename}' not found.")

    # Save .txt backup
    txt_path = path.with_suffix(".txt")
    txt_path.write_text(body.text, encoding="utf-8")

    # Regenerate PDF
    try:
        _regenerate_pdf(path, body.text)
        return {"message": f"'{filename}' updated and PDF regenerated.", "filename": filename}
    except Exception as e:
        return {
            "message": f"Text saved as .txt (PDF regeneration failed: {e})",
            "filename": txt_path.name,
        }


@router.delete("/cover-letters/{filename}")
def delete_cover_letter(filename: str, auth=Depends(_auth)):
    path = _cl_dir() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"'{filename}' not found.")
    path.unlink()
    # Also remove .txt backup if present
    txt = path.with_suffix(".txt")
    if txt.exists():
        txt.unlink()
    return {"message": f"'{filename}' deleted."}


@router.get("/cover-letters/{filename}/download")
def download_cover_letter(filename: str, auth=Depends(_auth)):
    path = _cl_dir() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"'{filename}' not found.")
    return FileResponse(
        path=str(path),
        media_type="application/pdf" if path.suffix == ".pdf" else "text/plain",
        filename=filename,
    )
