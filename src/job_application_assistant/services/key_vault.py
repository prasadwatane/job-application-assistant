"""
Key Vault Service
==================
Stores LLM provider API keys encrypted at rest in SQLite.
Keys are NEVER returned in plaintext via any API endpoint.

Encryption : Fernet (AES-128-CBC + HMAC-SHA256)
Master key : Derived from VAULT_MASTER_KEY env var
             Falls back to FILES_AUTH_KEY if not set.

Supported providers
-------------------
  groq        groq/llama-3.3-70b-versatile   (free, fast)
  anthropic   anthropic/claude-sonnet-4-5     (paid, best quality)
  openai      openai/gpt-4o-mini              (paid)
  gemini      gemini/gemini-2.0-flash         (free tier)
  ollama      ollama/<model>                  (local, no key needed)

Safe for GitHub: only ciphertext ever touches the DB or logs.
"""

import base64, hashlib, os, sqlite3
from datetime import datetime
from typing import Optional

DB_PATH = "job_applications.db"

PROVIDER_DEFAULTS = {
    "groq":      "groq/llama-3.3-70b-versatile",
    "anthropic": "anthropic/claude-sonnet-4-5",
    "openai":    "openai/gpt-4o-mini",
    "gemini":    "gemini/gemini-2.0-flash",
    "ollama":    "ollama/llama3.2",
}

_CREATE = """
CREATE TABLE IF NOT EXISTS provider_keys (
    provider      TEXT PRIMARY KEY,
    encrypted_key TEXT,
    model         TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 0,
    added_at      TEXT NOT NULL
);
"""


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise RuntimeError("Run: pip install cryptography")
    raw = os.getenv("VAULT_MASTER_KEY") or os.getenv("FILES_AUTH_KEY", "changeme")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def bootstrap():
    c = _conn()
    c.execute(_CREATE)
    c.commit()
    c.close()
    _auto_load_env_keys()


def _auto_load_env_keys():
    env_map = {
        "groq":      "GROQ_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai":    "OPENAI_API_KEY",
        "gemini":    "GEMINI_API_KEY",
    }
    for provider, var in env_map.items():
        raw = os.getenv(var)
        if raw and not _get_raw(provider):
            try:
                save_key(provider, raw, set_active=(get_active_provider() is None))
                print(f"🔑  Auto-imported {var} into vault ({provider})")
            except Exception as e:
                print(f"⚠️   Could not import {var}: {e}")


def save_key(provider: str, api_key: str, model: Optional[str] = None, set_active: bool = False):
    provider = provider.lower()
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"Unknown provider '{provider}'. Valid: {list(PROVIDER_DEFAULTS)}")
    model = model or PROVIDER_DEFAULTS[provider]
    enc = _encrypt(api_key) if api_key else ""
    now = datetime.utcnow().isoformat()
    c = _conn()
    c.execute(
        """INSERT INTO provider_keys (provider, encrypted_key, model, is_active, added_at)
           VALUES (?, ?, ?, 0, ?)
           ON CONFLICT(provider) DO UPDATE SET
             encrypted_key=excluded.encrypted_key,
             model=excluded.model,
             added_at=excluded.added_at""",
        (provider, enc, model, now),
    )
    if set_active:
        c.execute("UPDATE provider_keys SET is_active=0")
        c.execute("UPDATE provider_keys SET is_active=1 WHERE provider=?", (provider,))
    c.commit()
    c.close()
    if api_key:
        _inject_env(provider, api_key)


def set_active_provider(provider: str):
    provider = provider.lower()
    c = _conn()
    c.execute("UPDATE provider_keys SET is_active=0")
    cur = c.execute("UPDATE provider_keys SET is_active=1 WHERE provider=?", (provider,))
    c.commit()
    c.close()
    if cur.rowcount == 0:
        raise ValueError(f"Provider '{provider}' not in vault.")
    rec = _get_raw(provider)
    if rec and rec["encrypted_key"]:
        _inject_env(provider, _decrypt(rec["encrypted_key"]))


def delete_provider(provider: str) -> bool:
    c = _conn()
    cur = c.execute("DELETE FROM provider_keys WHERE provider=?", (provider,))
    c.commit()
    c.close()
    return cur.rowcount > 0


def _get_raw(provider: str) -> Optional[dict]:
    c = _conn()
    row = c.execute("SELECT * FROM provider_keys WHERE provider=?", (provider,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_active_provider() -> Optional[dict]:
    c = _conn()
    row = c.execute("SELECT * FROM provider_keys WHERE is_active=1").fetchone()
    c.close()
    return dict(row) if row else None


def list_providers_safe() -> list:
    bootstrap()
    c = _conn()
    rows = c.execute("SELECT * FROM provider_keys ORDER BY added_at DESC").fetchall()
    c.close()
    result = []
    for row in rows:
        d = dict(row)
        masked = "—"
        if d.get("encrypted_key"):
            try:
                plain = _decrypt(d["encrypted_key"])
                masked = "•" * max(0, len(plain) - 4) + plain[-4:]
            except Exception:
                masked = "••••[error]"
        d["key_preview"] = masked
        del d["encrypted_key"]
        result.append(d)
    return result


def build_llm(provider: Optional[str] = None):
    from crewai import LLM
    rec = _get_raw(provider) if provider else get_active_provider()
    if not rec:
        raise RuntimeError(
            "No LLM provider configured. "
            "POST /settings/providers to add one."
        )
    if rec.get("encrypted_key"):
        _inject_env(rec["provider"], _decrypt(rec["encrypted_key"]))
    return LLM(model=rec["model"], temperature=0.2, max_retries=3)


def _inject_env(provider: str, api_key: str):
    env_map = {
        "groq": "GROQ_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    var = env_map.get(provider)
    if var and api_key:
        os.environ[var] = api_key
