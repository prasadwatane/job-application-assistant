"""
Job Ingestion Service
======================
Accepts job postings in any format and normalises them into a standard dict:

  {
    "company_name": str,
    "role":         str,
    "description":  str,   # full clean text
    "url":          str | None,
    "source":       str,   # "text" | "pdf" | "csv" | "url"
  }

Supported input formats
-----------------------
  ingest_text(company, role, description, url)  → dict
  ingest_pdf(pdf_bytes, company, role, url)     → dict
  ingest_csv(file_bytes_or_path, ...)           → list[dict]
  ingest_url(url, company, role)                → dict

All functions raise ValueError on bad input so the API layer can return 400.
"""

import io
import re
from pathlib import Path
from typing import Union


# ── Text ───────────────────────────────────────────────────────────────────

def ingest_text(
    company_name: str,
    role: str,
    description: str,
    url: str | None = None,
) -> dict:
    """Accept plain-text job description. Minimum viable format."""
    if not description or len(description.strip()) < 50:
        raise ValueError("Job description is too short (< 50 chars). Paste the full JD.")
    return {
        "company_name": company_name.strip(),
        "role":         role.strip(),
        "description":  _clean_text(description),
        "url":          url,
        "source":       "text",
    }


# ── PDF ────────────────────────────────────────────────────────────────────

def ingest_pdf(
    pdf_bytes: bytes,
    company_name: str,
    role: str,
    url: str | None = None,
) -> dict:
    """Extract text from a PDF job posting."""
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        raise RuntimeError("PyPDF2 not installed. Run: pip install PyPDF2")

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            pages.append(t)
    full_text = "\n".join(pages)

    if not full_text.strip():
        raise ValueError("Could not extract text from PDF. It may be a scanned image.")

    return {
        "company_name": company_name.strip(),
        "role":         role.strip(),
        "description":  _clean_text(full_text),
        "url":          url,
        "source":       "pdf",
    }


# ── CSV / XLSX ─────────────────────────────────────────────────────────────

_COLUMN_ALIASES: dict[str, str] = {
    # company
    "company":         "company_name",
    "company name":    "company_name",
    "employer":        "company_name",
    "organisation":    "company_name",
    "organization":    "company_name",
    # role
    "role":            "role",
    "job role":        "role",
    "job title":       "role",
    "position":        "role",
    "title":           "role",
    # description
    "description":     "description",
    "job description": "description",
    "jd":              "description",
    "details":         "description",
    # url
    "url":             "url",
    "link":            "url",
    "job url":         "url",
    "posting url":     "url",
}


def ingest_csv(
    file_content: Union[bytes, str, Path],
    file_extension: str = ".csv",
) -> list[dict]:
    """
    Parse a CSV or XLSX file containing job listings.

    Required columns (case-insensitive, aliases supported):
      company_name, role, description

    Optional:
      url
    """
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas not installed. Run: pip install pandas openpyxl")

    if isinstance(file_content, Path):
        df = _read_df(file_content, file_extension)
    elif isinstance(file_content, bytes):
        buf = io.BytesIO(file_content)
        df = _read_df(buf, file_extension)
    else:
        df = _read_df(io.StringIO(file_content), ".csv")

    # normalise column names
    df.columns = [c.lower().strip() for c in df.columns]
    rename = {col: _COLUMN_ALIASES[col] for col in df.columns if col in _COLUMN_ALIASES}
    df = df.rename(columns=rename)

    required = {"company_name", "role", "description"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. "
            f"Found: {list(df.columns)}. "
            f"Accepted aliases: {list(_COLUMN_ALIASES.keys())}"
        )

    df = df.dropna(subset=list(required))
    if df.empty:
        raise ValueError("File contains no valid rows after removing blanks.")

    results = []
    for _, row in df.iterrows():
        desc = str(row["description"]).strip()
        if len(desc) < 50:
            continue  # skip obviously empty rows
        results.append({
            "company_name": str(row["company_name"]).strip(),
            "role":         str(row["role"]).strip(),
            "description":  _clean_text(desc),
            "url":          str(row.get("url", "")).strip() or None,
            "source":       "csv",
        })

    if not results:
        raise ValueError("No valid job entries found in the file.")

    return results


def _read_df(source, ext: str):
    import pandas as pd
    ext = ext.lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(source)
    return pd.read_csv(source)


# ── URL ────────────────────────────────────────────────────────────────────

def ingest_url(
    url: str,
    company_name: str,
    role: str,
    timeout: int = 15,
) -> dict:
    """
    Fetch a job posting URL and extract visible text.

    Uses httpx for the HTTP request and a lightweight regex-based
    text extractor (no heavy dependencies like BeautifulSoup required,
    but install httpx: pip install httpx).
    """
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx not installed. Run: pip install httpx")

    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; JobApplicationAssistant/1.0)"
                )
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"Failed to fetch URL: {exc}")

    raw_html = response.text
    text = _strip_html(raw_html)

    if len(text.strip()) < 100:
        raise ValueError(
            "Could not extract meaningful text from the URL. "
            "The page may require JavaScript — paste the JD text directly instead."
        )

    return {
        "company_name": company_name.strip(),
        "role":         role.strip(),
        "description":  _clean_text(text),
        "url":          url,
        "source":       "url",
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Basic HTML → plain text. Preserves newlines at block elements."""
    # replace block elements with newlines
    html = re.sub(r"<(?:br|p|div|li|h[1-6]|tr)[^>]*>", "\n", html, flags=re.IGNORECASE)
    # remove all remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # decode common HTML entities
    for entity, char in [
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"'),
    ]:
        html = html.replace(entity, char)
    return html


def _clean_text(text: str) -> str:
    """Normalise whitespace while preserving paragraph structure."""
    # collapse runs of spaces/tabs
    text = re.sub(r"[ \t]+", " ", text)
    # collapse runs of 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
