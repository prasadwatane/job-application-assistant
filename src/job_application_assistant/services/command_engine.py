"""
Command Engine Service
=======================
Two-layer prompt system for every Claude agent call:

  Layer 1 — ATS defaults (always on, mandatory)
    Keyword density, action verbs, quantification, section order,
    bullet formatting, cover letter structure.

  Layer 2 — User commands (optional, stacked on top)
    Free-text instructions stored in SQLite.
    Types:  style | content | override | redo
    Scopes: global (all jobs) | job (specific company+role)

Quick start
-----------
  from services.command_engine import CommandEngine
  engine = CommandEngine()
  engine.add_command("highlight Python and FastAPI", type="content")
  engine.add_command("formal British English", type="style")
  prompt = engine.build_enriched_prompt(job_description, company="BMW", role="ML Eng")
  # → inject prompt as system message into Claude agent calls
"""

import sqlite3
from datetime import datetime
from typing import Optional

DB_PATH = "job_applications.db"

# ── ATS rule bank ──────────────────────────────────────────────────────────

ATS_DEFAULTS: dict[str, list[str]] = {
    "section_order": [
        "Professional Summary",
        "Work Experience",
        "Technical Skills",
        "Education",
        "Certifications / Awards",
        "Projects (if relevant)",
    ],
    "bullet_rules": [
        "Begin every bullet with a strong action verb: Built, Led, Designed, Reduced, Increased, Automated, Delivered, Improved, Architected, Launched",
        "Keep bullets to 1-2 lines max",
        "Past tense for previous roles; present tense for current role only",
        "Quantify every achievement — use numbers, %, time saved, team size",
        "No personal pronouns: never use I, me, my, we in resume bullets",
        "One idea per bullet — no compound bullets joined with semicolons",
    ],
    "keyword_rules": [
        "Mirror exact terminology from the job description verbatim (e.g. 'machine learning' not 'ML' if JD says 'machine learning')",
        "Include the job title from the posting naturally in the summary section",
        "Prioritise 'Required' skills over 'Nice to have' skills in ordering",
        "Repeat the top 3 most frequent JD keywords at least twice each across the resume",
    ],
    "formatting_rules": [
        "Consistent date format: Mon YYYY – Mon YYYY (e.g. Jan 2022 – Mar 2024)",
        "Spell out all acronyms on first use",
        "No tables, columns, headers/footers, text boxes — ATS cannot parse them",
        "Font: 10–12pt, standard only (Arial, Calibri, Georgia, Times New Roman)",
        "Margins: 0.5in minimum; no decorative lines or graphics",
    ],
    "cover_letter_rules": [
        "Open with a hook — never start with 'I am writing to apply for...'",
        "Target length: 250–350 words total",
        "Name at least one specific product, initiative, or value from the company",
        "Demonstrate awareness of the company's current challenges or goals",
        "Close with a clear, direct call to action requesting an interview",
        "Match the formality level of the company's own job posting language",
    ],
}


def _render_ats_block() -> str:
    lines = ["=== ATS COMPLIANCE RULES — MANDATORY, APPLY TO ALL OUTPUT ===\n"]
    labels = {
        "section_order":    "RESUME SECTION ORDER",
        "bullet_rules":     "BULLET POINT RULES",
        "keyword_rules":    "KEYWORD RULES",
        "formatting_rules": "FORMATTING RULES",
        "cover_letter_rules": "COVER LETTER RULES",
    }
    for key, label in labels.items():
        lines.append(f"{label}:")
        items = ATS_DEFAULTS[key]
        if key == "section_order":
            for i, s in enumerate(items, 1):
                lines.append(f"  {i}. {s}")
        else:
            for r in items:
                lines.append(f"  • {r}")
        lines.append("")
    lines.append("=== END ATS RULES ===")
    return "\n".join(lines)


# ── SQLite schema ──────────────────────────────────────────────────────────

_CREATE = """
CREATE TABLE IF NOT EXISTS user_commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command     TEXT    NOT NULL,
    type        TEXT    NOT NULL DEFAULT 'content',
    scope       TEXT    NOT NULL DEFAULT 'global',
    company     TEXT,
    job_role    TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    note        TEXT
);
"""

VALID_TYPES  = {"style", "content", "override", "redo"}
VALID_SCOPES = {"global", "job"}


def _init():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_CREATE)
    conn.commit()
    conn.close()


# ── CommandEngine class ────────────────────────────────────────────────────

class CommandEngine:
    """
    Manages user commands and builds enriched prompts for Claude agents.

    Write API
    ---------
    add_command(command, type, scope, company, job_role, note) → id
    deactivate_command(id) → bool
    clear_scope(scope) → count

    Read API
    --------
    list_commands(scope, company, job_role, active_only) → list[dict]

    Prompt API
    ----------
    build_enriched_prompt(job_description, company, job_role) → str
    build_redo_prompt(original_output, redo_command, company, job_role) → str
    """

    def __init__(self):
        _init()

    # ── Write ──────────────────────────────────────────────────────────────

    def add_command(
        self,
        command: str,
        type: str = "content",
        scope: str = "global",
        company: Optional[str] = None,
        job_role: Optional[str] = None,
        note: Optional[str] = None,
    ) -> int:
        """
        Store a new user command.

        Parameters
        ----------
        command  : Free-text instruction, e.g. "emphasise leadership experience"
        type     : 'style' | 'content' | 'override' | 'redo'
                   style    → tone, formality, language
                   content  → what to highlight or de-emphasise
                   override → replaces a specific ATS default rule
                   redo     → trigger re-generation with new instruction
        scope    : 'global' (all jobs) | 'job' (specific company+role only)
        company  : Required when scope='job'
        job_role : Required when scope='job'
        note     : Optional reminder for yourself
        """
        if type not in VALID_TYPES:
            raise ValueError(f"type must be one of {VALID_TYPES}")
        if scope not in VALID_SCOPES:
            raise ValueError(f"scope must be one of {VALID_SCOPES}")
        if scope == "job" and not (company and job_role):
            raise ValueError("scope='job' requires company and job_role")

        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            """
            INSERT INTO user_commands
                (command, type, scope, company, job_role, active, created_at, note)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (command, type, scope, company, job_role,
             datetime.utcnow().isoformat(), note),
        )
        cmd_id = cur.lastrowid
        conn.commit()
        conn.close()
        return cmd_id

    def deactivate_command(self, cmd_id: int) -> bool:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            "UPDATE user_commands SET active = 0 WHERE id = ?", (cmd_id,)
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0

    def clear_scope(self, scope: str = "global") -> int:
        """Deactivate all commands in a scope. Returns count deactivated."""
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            "UPDATE user_commands SET active = 0 WHERE scope = ? AND active = 1",
            (scope,),
        )
        conn.commit()
        conn.close()
        return cur.rowcount

    # ── Read ───────────────────────────────────────────────────────────────

    def list_commands(
        self,
        scope: Optional[str] = None,
        company: Optional[str] = None,
        job_role: Optional[str] = None,
        active_only: bool = True,
    ) -> list[dict]:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        q = "SELECT * FROM user_commands WHERE 1=1"
        params: list = []
        if active_only:
            q += " AND active = 1"
        if scope:
            q += " AND scope = ?"
            params.append(scope)
        if company:
            q += " AND (company IS NULL OR lower(company) = lower(?))"
            params.append(company)
        if job_role:
            q += " AND (job_role IS NULL OR lower(job_role) = lower(?))"
            params.append(job_role)
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _active_commands(
        self,
        company: Optional[str],
        job_role: Optional[str],
    ) -> list[dict]:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        global_cmds = conn.execute(
            "SELECT * FROM user_commands WHERE active=1 AND scope='global' ORDER BY created_at"
        ).fetchall()
        cmds = [dict(r) for r in global_cmds]
        if company and job_role:
            scoped = conn.execute(
                """SELECT * FROM user_commands
                   WHERE active=1 AND scope='job'
                     AND lower(company)=lower(?) AND lower(job_role)=lower(?)
                   ORDER BY created_at""",
                (company, job_role),
            ).fetchall()
            cmds.extend(dict(r) for r in scoped)
        conn.close()
        return cmds

    # ── Prompt builders ────────────────────────────────────────────────────

    def build_enriched_prompt(
        self,
        job_description: str = "",
        company: Optional[str] = None,
        job_role: Optional[str] = None,
    ) -> str:
        """
        Build the full system prompt to inject into Claude agent calls.

        Structure:
          1. ATS compliance block (mandatory)
          2. User commands grouped by type (if any)
          3. Job context line
        """
        cmds = self._active_commands(company, job_role)
        parts = [_render_ats_block()]

        if cmds:
            parts.append("\n=== USER CUSTOMISATION INSTRUCTIONS (apply on top of ATS rules) ===\n")
            by_type: dict[str, list[str]] = {}
            for c in cmds:
                by_type.setdefault(c["type"], []).append(c["command"])

            order = [
                ("override", "ATS OVERRIDES  (these supersede the ATS defaults above)"),
                ("style",    "STYLE PREFERENCES"),
                ("content",  "CONTENT EMPHASIS"),
                ("redo",     "REDO INSTRUCTIONS"),
            ]
            for t, label in order:
                if t in by_type:
                    parts.append(f"{label}:")
                    for c in by_type[t]:
                        parts.append(f"  → {c}")
                    parts.append("")
            parts.append("=== END USER INSTRUCTIONS ===")

        if company or job_role:
            parts.append(f"\nApplication target: {company or 'N/A'} — {job_role or 'N/A'}")

        return "\n".join(parts)

    def build_redo_prompt(
        self,
        original_output: str,
        redo_instruction: str,
        company: Optional[str] = None,
        job_role: Optional[str] = None,
    ) -> str:
        """
        Prompt for re-generating output with a specific revision instruction.
        Preserves all ATS rules and active commands, adds redo directive.
        """
        base = self.build_enriched_prompt(company=company, job_role=job_role)
        return (
            f"{base}\n\n"
            f"=== REDO INSTRUCTION ===\n"
            f"{redo_instruction}\n\n"
            f"=== ORIGINAL OUTPUT TO REVISE ===\n"
            f"{original_output}\n"
            f"=== END ORIGINAL ===\n\n"
            "Revise the above applying the redo instruction. "
            "All ATS rules and user instructions remain mandatory."
        )
