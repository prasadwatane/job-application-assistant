"""Settings Router — /settings — manage encrypted LLM provider keys."""

import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..services.key_vault import (
    save_key, set_active_provider, delete_provider,
    list_providers_safe, get_active_provider, PROVIDER_DEFAULTS,
)

router = APIRouter()
FILES_AUTH_KEY = os.getenv("FILES_AUTH_KEY", "changeme")


def _auth(key: str = Query(...)):
    if key != FILES_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid auth key.")


class AddProviderRequest(BaseModel):
    provider:   str
    api_key:    str = ""
    model:      Optional[str] = None
    set_active: bool = False


@router.get("/providers")
def list_providers(auth=Depends(_auth)):
    providers = list_providers_safe()
    active = get_active_provider()
    return {
        "active_provider": active["provider"] if active else None,
        "active_model":    active["model"]    if active else None,
        "providers":       providers,
    }


@router.post("/providers", status_code=201)
def add_provider(body: AddProviderRequest, auth=Depends(_auth)):
    try:
        save_key(body.provider, body.api_key, body.model, body.set_active)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "message":   f"Provider '{body.provider}' saved (key encrypted at rest).",
        "provider":  body.provider,
        "model":     body.model or PROVIDER_DEFAULTS.get(body.provider),
        "is_active": body.set_active,
    }


@router.post("/providers/{provider}/activate")
def activate_provider(provider: str, auth=Depends(_auth)):
    try:
        set_active_provider(provider)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": f"'{provider}' is now active."}


@router.delete("/providers/{provider}")
def remove_provider(provider: str, auth=Depends(_auth)):
    if not delete_provider(provider):
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found.")
    return {"message": f"Provider '{provider}' removed."}


@router.get("/providers/defaults")
def provider_defaults(auth=Depends(_auth)):
    return {"defaults": PROVIDER_DEFAULTS}
