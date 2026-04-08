"""Job Application Assistant — Prasad Watane"""

import uvicorn
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .routers import crew_router, files_router
from .routers.review_router    import router as review_router
from .routers.command_router   import router as command_router
from .routers.ingestion_router import router as ingestion_router
from .routers.settings_router  import router as settings_router
from .routers.documents_router import router as documents_router
from .services.email_monitor   import monitor
from .services.key_vault       import bootstrap as vault_bootstrap
from .infrastructure.job_log.job_log import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    vault_bootstrap()
    monitor.start()
    yield
    monitor.stop()


app = FastAPI(
    title="Job Application Assistant",
    description="AI-powered job application pipeline by Prasad Watane. Three agents: resume picker, resume tailor, cover letter writer.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(ingestion_router, prefix="/ingest",    tags=["Job Ingestion"])
app.include_router(command_router,   prefix="/commands",  tags=["Command Engine"])
app.include_router(review_router,    prefix="/review",    tags=["Human Review"])
app.include_router(settings_router,  prefix="/settings",  tags=["AI Providers"])
app.include_router(documents_router, prefix="/documents", tags=["Documents"])
app.include_router(crew_router.router, prefix="/crew",    tags=["Crew"])
app.include_router(files_router.router, prefix="/files",  tags=["Files"])

_UI = Path(__file__).parent.parent.parent / "dashboard.html"


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def serve_ui():
    if _UI.exists():
        return HTMLResponse(content=_UI.read_text(encoding="utf-8"))
    return HTMLResponse(content="<p>dashboard.html not found.</p>", status_code=404)


@app.get("/", tags=["Health"])
async def root():
    from .services.key_vault import get_active_provider
    active = get_active_provider()
    return {
        "name":    "Job Application Assistant",
        "author":  "Prasad Watane",
        "version": "2.0.0",
        "ui":      "http://localhost:8000/ui",
        "docs":    "http://localhost:8000/docs",
        "llm":     {"provider": active["provider"] if active else None,
                    "model":    active["model"]    if active else "not configured"},
        "pipeline": "resume_picker -> resume_tailor -> cover_letter_generator",
    }


@app.get("/email-monitor/status", tags=["Email Monitor"])
def email_monitor_status():
    return monitor.status()


@app.post("/email-monitor/poll", tags=["Email Monitor"])
def trigger_poll():
    from .services.email_monitor import poll_once
    events = poll_once()
    return {"processed": len(events), "events": events}


def run():
    uvicorn.run("src.job_application_assistant.main:app",
                host="0.0.0.0", port=8000, reload=True)
