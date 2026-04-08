import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter()
AUTH_KEY = os.getenv("FILES_AUTH_KEY", "changeme")
COVER_LETTERS_DIR = Path("generated_cover_letters")

def _auth(key: str = Query(...)):
    if key != AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid auth key.")

@router.get("/")
def list_files(key: str = Query(...)):
    _auth(key)
    files = list(COVER_LETTERS_DIR.glob("*.pdf")) + list(COVER_LETTERS_DIR.glob("*.txt"))
    return {"files": [f.name for f in files]}

@router.get("/download/{filename}")
def download(filename: str, key: str = Query(...)):
    _auth(key)
    path = COVER_LETTERS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path)
