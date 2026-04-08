"""
Job Ingestion Router  —  /ingest
==================================
Accepts job postings in any format and kicks off the crew pipeline.

Endpoints
---------
  POST /ingest/text   — plain text job description (JSON body)
  POST /ingest/pdf    — upload a PDF job posting
  POST /ingest/csv    — upload CSV or XLSX file of job listings (batch)
  POST /ingest/url    — provide a URL to a job posting

All endpoints:
  • Normalise input via job_ingestion service
  • Read PDF resumes from config.yml resume_directory
  • Fire the CrewAI pipeline asynchronously (background task)
  • Return log_id immediately; check status at GET /review/{id}

Require ?key=FILES_AUTH_KEY
"""

import io
import os
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel

from ..services import job_ingestion as ingest
from ..crew import run_crew_for_single_job

router = APIRouter()
FILES_AUTH_KEY = os.getenv("FILES_AUTH_KEY", "changeme")


def _auth(key: str = Query(...)):
    if key != FILES_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid auth key.")


def _read_resumes() -> str:
    try:
        with open("config.yml") as f:
            cfg = yaml.safe_load(f)
        resume_dir = Path(cfg.get("resume_directory", "resumes"))
    except Exception:
        resume_dir = Path("resumes")

    from PyPDF2 import PdfReader
    pages = []
    for pdf in resume_dir.glob("*.pdf"):
        try:
            reader = PdfReader(pdf)
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages.append(f"--- Resume: {pdf.name} ---\n{t}")
        except Exception as exc:
            print(f"⚠️  Could not read {pdf.name}: {exc}")
    if not pages:
        raise HTTPException(status_code=500, detail="No resumes found. Check resume_directory in config.yml")
    return "\n\n".join(pages)


def _run_bg(application: dict, resumes: str):
    """Background task wrapper — errors are logged, not raised."""
    try:
        run_crew_for_single_job(application, resumes)
    except Exception as exc:
        print(f"❌  Crew failed for {application.get('company_name')}: {exc}")


# ── Text ───────────────────────────────────────────────────────────────────

class TextJobInput(BaseModel):
    company_name: str
    role:         str
    description:  str
    url:          Optional[str] = None
    redo_instruction: Optional[str] = None


@router.post("/text", summary="Submit a job description as plain text")
def ingest_text(body: TextJobInput, bg: BackgroundTasks, auth=Depends(_auth)):
    """
    Paste the full job description as text.
    Minimum 50 characters required.
    """
    try:
        application = ingest.ingest_text(
            body.company_name, body.role, body.description, body.url
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    resumes = _read_resumes()
    bg.add_task(_run_bg, application, resumes)
    return {
        "message": "Job submitted. Crew is running in the background.",
        "company": body.company_name,
        "role":    body.role,
        "source":  "text",
        "next":    "GET /review/pending to check when results are ready.",
    }


# ── PDF ────────────────────────────────────────────────────────────────────

@router.post("/pdf", summary="Upload a PDF job posting")
async def ingest_pdf(
    bg:           BackgroundTasks,
    file:         UploadFile = File(...),
    company_name: str = Form(...),
    role:         str = Form(...),
    url:          Optional[str] = Form(None),
    auth=Depends(_auth),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    pdf_bytes = await file.read()
    try:
        application = ingest.ingest_pdf(pdf_bytes, company_name, role, url)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    resumes = _read_resumes()
    bg.add_task(_run_bg, application, resumes)
    return {
        "message": "PDF parsed and job submitted.",
        "company": company_name, "role": role, "source": "pdf",
        "next": "GET /review/pending",
    }


# ── CSV / XLSX ─────────────────────────────────────────────────────────────

@router.post("/csv", summary="Upload CSV or XLSX with multiple job listings")
async def ingest_csv(
    bg:   BackgroundTasks,
    file: UploadFile = File(...),
    auth=Depends(_auth),
):
    """
    Required columns (case-insensitive):  company_name, role, description
    Optional column:                       url

    Accepted aliases: 'company', 'employer', 'job title', 'position', 'jd', etc.
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in (".csv", ".tsv", ".xlsx", ".xls"):
        raise HTTPException(status_code=400, detail="Accepted formats: csv, tsv, xlsx, xls")

    content = await file.read()
    try:
        applications = ingest.ingest_csv(content, file_extension=ext)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    resumes = _read_resumes()
    for app in applications:
        bg.add_task(_run_bg, app, resumes)

    return {
        "message":     f"{len(applications)} job(s) submitted.",
        "count":       len(applications),
        "companies":   [a["company_name"] for a in applications],
        "source":      "csv",
        "next":        "GET /review/pending",
    }


# ── URL ────────────────────────────────────────────────────────────────────

class UrlJobInput(BaseModel):
    url:          str
    company_name: str
    role:         str


@router.post("/url", summary="Provide a URL to a job posting")
def ingest_url(body: UrlJobInput, bg: BackgroundTasks, auth=Depends(_auth)):
    """
    Fetches the URL and extracts the job description text.
    Falls back gracefully with an error if the page requires JavaScript.
    """
    try:
        application = ingest.ingest_url(body.url, body.company_name, body.role)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    resumes = _read_resumes()
    bg.add_task(_run_bg, application, resumes)
    return {
        "message": "URL fetched and job submitted.",
        "company": body.company_name, "role": body.role,
        "source":  "url", "url": body.url,
        "next":    "GET /review/pending",
    }
