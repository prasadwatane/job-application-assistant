"""
Command Router  —  /commands
==============================
API for managing user commands that override / extend the ATS defaults.

Endpoints
---------
  GET  /commands              list active commands
  POST /commands              add a new command
  POST /commands/redo/{id}    re-run an application with a redo instruction
  DELETE /commands/{id}       deactivate a command
  DELETE /commands/clear      deactivate all global commands
  GET  /commands/ats-defaults show the built-in ATS ruleset

All endpoints require ?key=FILES_AUTH_KEY
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..services.command_engine import CommandEngine, VALID_TYPES, VALID_SCOPES, _render_ats_block
from ..infrastructure.job_log import job_log as log_store

router = APIRouter()
FILES_AUTH_KEY = os.getenv("FILES_AUTH_KEY", "changeme")


def _auth(key: str = Query(...)):
    if key != FILES_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid auth key.")


class AddCommandRequest(BaseModel):
    command:  str
    type:     str = "content"          # style | content | override | redo
    scope:    str = "global"           # global | job
    company:  Optional[str] = None
    job_role: Optional[str] = None
    note:     Optional[str] = None


class RedoRequest(BaseModel):
    redo_instruction: str
    company:  Optional[str] = None
    job_role: Optional[str] = None


@router.get("", summary="List active commands")
def list_commands(
    scope:    Optional[str] = Query(None),
    company:  Optional[str] = Query(None),
    job_role: Optional[str] = Query(None),
    auth=Depends(_auth),
):
    engine = CommandEngine()
    cmds = engine.list_commands(scope=scope, company=company, job_role=job_role)
    return {"count": len(cmds), "commands": cmds}


@router.post("", summary="Add a new command", status_code=201)
def add_command(body: AddCommandRequest, auth=Depends(_auth)):
    """
    Add a user command.

    Command types
    -------------
    style    → tone, formality, language  e.g. "use formal British English"
    content  → what to emphasise          e.g. "highlight Python and FastAPI skills"
    override → replace an ATS rule        e.g. "omit the Professional Summary section"
    redo     → stored redo template       e.g. "rewrite bullets as STAR format"

    Scope
    -----
    global → applies to all future runs
    job    → applies to a specific company + role only (provide both)
    """
    engine = CommandEngine()
    try:
        cmd_id = engine.add_command(
            command=body.command, type=body.type, scope=body.scope,
            company=body.company, job_role=body.job_role, note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "message": f"Command #{cmd_id} added.",
        "id": cmd_id,
        "command": body.command,
        "type": body.type,
        "scope": body.scope,
        "hint": "It will be applied to the next crew run automatically.",
    }


@router.delete("/{cmd_id}", summary="Deactivate a command")
def deactivate_command(cmd_id: int, auth=Depends(_auth)):
    engine = CommandEngine()
    ok = engine.deactivate_command(cmd_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Command #{cmd_id} not found.")
    return {"message": f"Command #{cmd_id} deactivated."}


@router.delete("/clear/global", summary="Deactivate all global commands")
def clear_global(auth=Depends(_auth)):
    engine = CommandEngine()
    count = engine.clear_scope("global")
    return {"message": f"{count} global command(s) deactivated."}


@router.post("/redo/{log_id}", summary="Re-run a crew job with a new instruction")
def redo_application(log_id: int, body: RedoRequest, auth=Depends(_auth)):
    """
    Trigger a re-run of an existing application with a specific redo instruction.
    The original ATS rules and active commands are all preserved.
    A new job log entry is created (pending_review) alongside the original.
    """
    from pathlib import Path as P
    from PyPDF2 import PdfReader
    import yaml

    app = log_store.get_application(log_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"Application #{log_id} not found.")

    # read resume directory from config
    try:
        with open("config.yml") as f:
            cfg = yaml.safe_load(f)
        resume_dir = P(cfg.get("resume_directory", "resumes"))
        pages = []
        for pdf in resume_dir.glob("*.pdf"):
            reader = PdfReader(pdf)
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages.append(f"--- {pdf.name} ---\n{t}")
        all_resumes = "\n\n".join(pages)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read resumes: {exc}")

    from ..crew import run_crew_for_single_job
    application = {
        "company_name": body.company or app["company_name"],
        "role":         body.job_role or app["job_role"],
        "description":  app.get("job_description", ""),
        "url":          app.get("url"),
        "source":       "redo",
    }
    try:
        result = run_crew_for_single_job(
            application=application,
            all_resumes_text=all_resumes,
            redo_instruction=body.redo_instruction,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "message": f"Redo complete. New entry created (id={result['log_id']}).",
        "original_id": log_id,
        **result,
    }


@router.get("/ats-defaults", summary="Show the built-in ATS ruleset")
def ats_defaults(auth=Depends(_auth)):
    """Returns the full ATS compliance ruleset applied to every crew run by default."""
    from ..services.command_engine import ATS_DEFAULTS
    return {
        "description": "These rules are always active. Use 'override' commands to change them.",
        "rules": ATS_DEFAULTS,
        "rendered_prompt_block": _render_ats_block(),
    }
