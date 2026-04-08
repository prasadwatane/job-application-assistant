"""
Cover Letter PDF Generator Tool
=================================
CrewAI BaseTool used by the Cover Letter Manager agent.
Accepts the letter body as plain text, renders it to a
professional A4 PDF with consistent typography, and saves it
to the generated_cover_letters/ directory.

Returns the absolute file path of the saved PDF.
"""

import os
import textwrap
from pathlib import Path
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

OUTPUT_DIR = Path(os.getenv("COVER_LETTERS_DIR", "generated_cover_letters"))


class CoverLetterInput(BaseModel):
    file_name: str = Field(
        ...,
        description="File name in format 'Company - Job Role.pdf' e.g. 'BMW - ML Engineer.pdf'",
    )
    cover_letter_body: str = Field(
        ...,
        description="Full cover letter text. Include sender info, date, greeting, body, and closing.",
    )


class GenerateCoverLetterPDFTool(BaseTool):
    name: str = "Generate Cover Letter PDF"
    description: str = (
        "Saves a cover letter as a formatted PDF file. "
        "Provide the file name (Company - Role.pdf) and the full letter text. "
        "Returns the absolute path to the saved file."
    )
    args_schema: Type[BaseModel] = CoverLetterInput

    def _run(self, file_name: str, cover_letter_body: str) -> str:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Sanitise filename
        safe_name = _safe_filename(file_name)
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        out_path = OUTPUT_DIR / safe_name

        try:
            _render_pdf(out_path, cover_letter_body)
            return str(out_path.resolve())
        except Exception as exc:
            # Fallback: save as plain text if reportlab unavailable
            txt_path = out_path.with_suffix(".txt")
            txt_path.write_text(cover_letter_body, encoding="utf-8")
            return f"{txt_path.resolve()} (saved as text — install reportlab for PDF: {exc})"


# ── PDF rendering ──────────────────────────────────────────────────────────

def _render_pdf(out_path: Path, body: str) -> None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib import colors
    except ImportError as exc:
        raise ImportError("reportlab is required for PDF generation. Run: pip install reportlab") from exc

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )

    styles = getSampleStyleSheet()

    header_style = ParagraphStyle(
        "Header",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1a1a1a"),
        spaceAfter=2,
    )

    date_style = ParagraphStyle(
        "Date",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#555555"),
        spaceAfter=14,
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=16,
        alignment=TA_JUSTIFY,
        textColor=colors.HexColor("#1a1a1a"),
        spaceAfter=10,
    )

    bold_body_style = ParagraphStyle(
        "BoldBody",
        parent=body_style,
        fontName="Helvetica-Bold",
    )

    story = []
    lines = body.strip().split("\n")
    header_done = False
    blank_after_header = 0

    for line in lines:
        stripped = line.strip()

        # First 4 non-blank lines = header (sender info + date)
        if not header_done and blank_after_header < 1:
            if stripped == "":
                blank_after_header += 1
                story.append(Spacer(1, 6))
            else:
                # Detect date line (contains digits and common date words)
                import re
                is_date = bool(re.search(r"\d{4}|\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", stripped))
                style = date_style if is_date else header_style
                story.append(Paragraph(_escape(stripped), style))
            if blank_after_header >= 1:
                header_done = True
            continue

        if stripped == "":
            story.append(Spacer(1, 8))
            continue

        # Process inline bold markers (**text**)
        rendered = _process_bold(stripped)
        story.append(Paragraph(rendered, body_style))

    doc.build(story)


def _process_bold(text: str) -> str:
    """Convert **text** markdown to ReportLab <b> tags."""
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _escape(text))


def _escape(text: str) -> str:
    """Escape HTML special chars for ReportLab Paragraph."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _safe_filename(name: str) -> str:
    """Remove characters that are unsafe in filenames."""
    import re
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return name.strip()
