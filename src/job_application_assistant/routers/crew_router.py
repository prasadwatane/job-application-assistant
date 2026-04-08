from fastapi import APIRouter
router = APIRouter()

@router.get("/status")
def status():
    return {"status": "use /ingest endpoints to submit jobs"}
