"""
Resume PDF Saver Tool
======================
CrewAI BaseTool used by the Resume Tailor agent.
Takes the tailored resume as structured text, renders it to a
professional A4 PDF, and saves it to generated_resumes/ directory.
Returns the absolute file path.
"""

import os
import re
from pathlib import Path
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

OUTPUT_DIR = Path(os.getenv("RESUMES_OUTPUT_DIR", "generated_resumes"))


class ResumeSaverInput(BaseModel):
    file_name: str = Field(
        ...,
        description="File name e.g. 'Prasad_Watane_Google_DataScientist.pdf'",
    )
    resume_body: str = Field(
        ...,
        description="Full tailored resume text content.",
    )


class GenerateResumePDFTool(BaseTool):
    name: str = "Generate Tailored Resume PDF"
    description: str = (
        "Saves a tailored resume as a formatted PDF file. "
        "Provide the file name and the full resume text. "
        "Returns the absolute path to the saved file."
    )
    args_schema: Type[BaseModel] = ResumeSaverInput

    def _run(self, file_name: str, resume_body: str) -> str:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[\\/*?:"<>|]', "", file_name).strip()
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        out_path = OUTPUT_DIR / safe
        try:
            _render_resume_pdf(out_path, resume_body)
            return str(out_path.resolve())
        except Exception as exc:
            txt = out_path.with_suffix(".txt")
            txt.write_text(resume_body, encoding="utf-8")
            return f"{txt.resolve()} (text fallback — install reportlab: {exc})"


def _render_resume_pdf(out_path: Path, body: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib import colors

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )

    name_style = ParagraphStyle("Name", fontName="Helvetica-Bold",
        fontSize=16, leading=20, textColor=colors.HexColor("#0a0a0f"),
        alignment=TA_CENTER, spaceAfter=4)
    contact_style = ParagraphStyle("Contact", fontName="Helvetica",
        fontSize=9, leading=13, textColor=colors.HexColor("#555555"),
        alignment=TA_CENTER, spaceAfter=10)
    section_style = ParagraphStyle("Section", fontName="Helvetica-Bold",
        fontSize=10, leading=14, textColor=colors.HexColor("#0a0a0f"),
        spaceBefore=10, spaceAfter=3)
    body_style = ParagraphStyle("Body", fontName="Helvetica",
        fontSize=9.5, leading=14, textColor=colors.HexColor("#1a1a1a"),
        spaceAfter=4)
    bullet_style = ParagraphStyle("Bullet", fontName="Helvetica",
        fontSize=9.5, leading=14, textColor=colors.HexColor("#1a1a1a"),
        leftIndent=12, spaceAfter=3)

    def esc(t):
        return t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    def proc(line):
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(line))
        return line

    story = []
    lines = body.strip().split("\n")
    i = 0
    first_line = True

    while i < len(lines):
        line = lines[i].rstrip()

        # Blank line
        if not line.strip():
            story.append(Spacer(1, 5))
            i += 1
            continue

        # First non-blank = name
        if first_line:
            story.append(Paragraph(esc(line.strip()), name_style))
            first_line = False
            i += 1
            # Next lines until blank = contact
            contact_parts = []
            while i < len(lines) and lines[i].strip():
                contact_parts.append(esc(lines[i].strip()))
                i += 1
            if contact_parts:
                story.append(Paragraph(" | ".join(contact_parts), contact_style))
            story.append(HRFlowable(width="100%", thickness=0.5,
                color=colors.HexColor("#cccccc"), spaceAfter=6))
            continue

        stripped = line.strip()

        # Section headers — ALL CAPS or ends with colon
        if (stripped.isupper() and len(stripped) > 3) or (stripped.endswith(":") and len(stripped) < 40):
            story.append(HRFlowable(width="100%", thickness=0.3,
                color=colors.HexColor("#dddddd"), spaceBefore=4, spaceAfter=2))
            story.append(Paragraph(esc(stripped.rstrip(":")), section_style))
        # Bullet points
        elif stripped.startswith(("-", "•", "*", "+")):
            content = stripped.lstrip("-•*+ ").strip()
            story.append(Paragraph("• " + proc(content), bullet_style))
        # Regular line
        else:
            story.append(Paragraph(proc(stripped), body_style))

        i += 1

    doc.build(story)
