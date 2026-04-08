"""
API Schemas
============
All Pydantic request/response models for the Job Application Assistant API.
"""

from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Job ingestion ──────────────────────────────────────────────────────────

class TextJobInput(BaseModel):
    company_name:     str
    role:             str
    description:      str = Field(..., min_length=50, description="Full job description text")
    url:              Optional[str] = None
    redo_instruction: Optional[str] = None


class UrlJobInput(BaseModel):
    url:          str
    company_name: str
    role:         str


class IngestionResponse(BaseModel):
    message:  str
    company:  str
    role:     str
    source:   str  # text | pdf | csv | url
    next:     str


# ── Crew run result ────────────────────────────────────────────────────────

class CrewJobResult(BaseModel):
    log_id:            int
    status:            str = "pending_review"
    company_name:      str
    job_role:          str
    fit_score:         float
    ats_score:         float
    best_resume_name:  str
    cover_letter_path: str
    skills_to_learn:   list[str]
    reasoning:         str
    ats_report:        dict
    message:           str


# ── Commands ───────────────────────────────────────────────────────────────

class AddCommandRequest(BaseModel):
    command:  str = Field(..., description="Free-text instruction for the agents")
    type:     str = Field("content", description="style | content | override | redo")
    scope:    str = Field("global",  description="global | job")
    company:  Optional[str] = None
    job_role: Optional[str] = None
    note:     Optional[str] = None


class CommandResponse(BaseModel):
    id:      int
    command: str
    type:    str
    scope:   str
    active:  bool
    created_at: str


class RedoRequest(BaseModel):
    redo_instruction: str
    company:  Optional[str] = None
    job_role: Optional[str] = None


# ── Human review ───────────────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    reviewer_notes: Optional[str] = None


class RejectRequest(BaseModel):
    reviewer_notes: str = Field(..., description="Rejection reason — required to keep the log useful")


class CoverLetterUpdate(BaseModel):
    cover_letter_path: str


class ApplicationLogEntry(BaseModel):
    id:                int
    company_name:      str
    job_role:          str
    url:               Optional[str]
    fit_score:         Optional[float]
    ats_score:         Optional[float]
    best_resume_name:  Optional[str]
    skills_to_learn:   list[str] = []
    reasoning:         Optional[str]
    cover_letter_path: Optional[str]
    ats_report:        Optional[dict]
    status:            str
    reviewer_notes:    Optional[str]
    source:            Optional[str]
    created_at:        str
    reviewed_at:       Optional[str]
    submitted_at:      Optional[str]


class ApplicationStats(BaseModel):
    total:               int
    pending_review:      int = 0
    approved:            int = 0
    rejected:            int = 0
    submitted:           int = 0
    interview_invited:   int = 0
    rejected_by_company: int = 0
    awaiting_response:   int = 0
    avg_fit_score:       float = 0.0
    avg_ats_score:       float = 0.0


# ── Email events ───────────────────────────────────────────────────────────

class EmailEvent(BaseModel):
    id:              int
    application_id:  Optional[int]
    message_id:      Optional[str]
    sender:          Optional[str]
    subject:         Optional[str]
    received_at:     Optional[str]
    snippet:         Optional[str]
    classification:  Optional[str]   # interview_invite | rejection | follow_up | acknowledgement | other
    confidence_note: Optional[str]
    processed_at:    Optional[str]


# ── ATS report ─────────────────────────────────────────────────────────────

class ATSReportSchema(BaseModel):
    keyword_score:       float
    bullet_score:        float
    overall_score:       float
    passed:              bool
    missing_keywords:    list[str]
    weak_bullets:        list[str]
    cover_letter_issues: list[str]
    suggestions:         list[str]


# ── Legacy (kept for backward compatibility with crew_router) ──────────────

class CrewRunResponse(BaseModel):
    status:  str
    message: str


class SingleJobInput(BaseModel):
    company_name:    str
    job_role:        str
    job_description: str
    url:             Optional[str] = None


class FileUploadResponse(BaseModel):
    status:    str
    message:   str
    file_path: str
