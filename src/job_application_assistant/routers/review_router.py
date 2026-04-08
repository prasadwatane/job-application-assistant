"""
Human Review Router  —  /review
=================================
Control panel for the human-in-the-loop workflow.

  GET  /review/pending            applications waiting for your approval
  GET  /review/log                full history (filter by status)
  GET  /review/stats              counts + averages by status
  GET  /review/{id}               single application detail
  POST /review/{id}/approve       approve cover letter
  POST /review/{id}/reject        reject (reason required)
  POST /review/{id}/mark-submitted confirm you sent the application
  PUT  /review/{id}/cover-letter  swap cover letter path (after manual edit)
  GET  /review/{id}/emails        email events for this application

Require ?key=FILES_AUTH_KEY
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..infrastructure.job_log import job_log as log_store
from ..services.email_monitor import list_email_events

router = APIRouter()
FILES_AUTH_KEY = os.getenv("FILES_AUTH_KEY", "changeme")


def _auth(key: str = Query(...)):
    if key != FILES_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid auth key.")


class ApproveRequest(BaseModel):
    reviewer_notes: Optional[str] = None


class RejectRequest(BaseModel):
    reviewer_notes: str  # mandatory — keep your log useful


class CoverLetterUpdate(BaseModel):
    cover_letter_path: str


@router.get("/pending")
def list_pending(auth=Depends(_auth)):
    items = log_store.list_pending()
    return {
        "count": len(items), "pending": items,
        "hint": "POST /review/{id}/approve or /reject to action each item.",
    }


@router.get("/log")
def application_log(
    filter_status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    auth=Depends(_auth),
):
    try:
        items = log_store.list_all(status=filter_status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"count": len(items), "applications": items}


@router.get("/stats")
def stats(auth=Depends(_auth)):
    return log_store.summary_stats()


@router.get("/{log_id}")
def get_application(log_id: int, auth=Depends(_auth)):
    item = log_store.get_application(log_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Application #{log_id} not found.")
    return item


@router.post("/{log_id}/approve")
def approve(log_id: int, body: ApproveRequest, auth=Depends(_auth)):
    item = log_store.get_application(log_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"#{log_id} not found.")
    if item["status"] != "pending_review":
        raise HTTPException(status_code=409, detail=f"Status is '{item['status']}', expected 'pending_review'.")
    if not log_store.approve_application(log_id, body.reviewer_notes):
        raise HTTPException(status_code=500, detail="Update failed.")
    return {
        "message": f"#{log_id} approved.",
        "next_step": f"POST /review/{log_id}/mark-submitted once sent.",
        "cover_letter_path": item.get("cover_letter_path"),
    }


@router.post("/{log_id}/reject")
def reject(log_id: int, body: RejectRequest, auth=Depends(_auth)):
    item = log_store.get_application(log_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"#{log_id} not found.")
    if item["status"] != "pending_review":
        raise HTTPException(status_code=409, detail=f"Status is '{item['status']}'.")
    if not log_store.reject_application(log_id, body.reviewer_notes):
        raise HTTPException(status_code=500, detail="Update failed.")
    return {"message": f"#{log_id} rejected.", "reason": body.reviewer_notes}


@router.post("/{log_id}/mark-submitted")
def mark_submitted(log_id: int, auth=Depends(_auth)):
    item = log_store.get_application(log_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"#{log_id} not found.")
    if item["status"] != "approved":
        raise HTTPException(status_code=409, detail=f"Must be 'approved' first. Current: '{item['status']}'.")
    if not log_store.mark_submitted(log_id):
        raise HTTPException(status_code=500, detail="Update failed.")
    return {
        "message": f"#{log_id} marked as submitted. Good luck!",
        "company": item["company_name"], "role": item["job_role"],
    }


@router.put("/{log_id}/cover-letter")
def update_cover_letter(log_id: int, body: CoverLetterUpdate, auth=Depends(_auth)):
    if not Path(body.cover_letter_path).exists():
        raise HTTPException(status_code=400, detail=f"File not found: {body.cover_letter_path}")
    if not log_store.get_application(log_id):
        raise HTTPException(status_code=404, detail=f"#{log_id} not found.")
    if not log_store.update_cover_letter_path(log_id, body.cover_letter_path):
        raise HTTPException(status_code=500, detail="Update failed.")
    return {"message": "Cover letter path updated.", "path": body.cover_letter_path}


@router.get("/{log_id}/emails")
def get_email_events(log_id: int, auth=Depends(_auth)):
    if not log_store.get_application(log_id):
        raise HTTPException(status_code=404, detail=f"#{log_id} not found.")
    events = list_email_events(application_id=log_id)
    return {"count": len(events), "events": events}
