import asyncio, json, re
from datetime import datetime
from typing import Optional
from crewai import Agent, Crew, LLM, Process, Task
from pydantic import BaseModel, Field
from .infrastructure.job_log.job_log import ApplicationRecord, insert_application
from .services.command_engine import CommandEngine
from .services.ats_optimizer import score_and_report
from .services.email_sender import notify_review_ready, notify_ats_warning
from .services.key_vault import build_llm
from .tools.cover_letter_saver import GenerateCoverLetterPDFTool
from .tools.resume_saver import GenerateResumePDFTool
from .tools.push_tool import PushNotificationTool

class FinalOutput(BaseModel):
    cover_letter_path:    str = ""
    tailored_resume_path: str = ""
    reasoning:            str = ""
    fit_score:            float = 0
    skills_to_learn:      list = []
    best_resume_name:     str = ""
    tailoring_notes:      str = ""

def _make_crew(enriched_prompt: str):
    llm = build_llm()

    resume_manager = Agent(
        role="Experienced Recruiter with deep ATS knowledge",
        goal="Choose the single best resume from the list based on the job description",
        backstory=(
            "You are a brutally honest recruiter who scores resumes 0-100 based on "
            "Education Background, Relevant Skills, Work Experience, Domain Fit, "
            "and Overall Relevance. You operate in a human-in-the-loop system — "
            "be specific and honest. Follow all ATS rules in the prompt."
        ),
        verbose=True, llm=llm,
    )

    resume_tailor = Agent(
        role="Expert Resume Writer who tailors resumes for specific job descriptions",
        goal="Rewrite the chosen resume to maximise ATS keyword match for this specific role without inventing anything",
        backstory=(
            "You are a master at restructuring genuine experience to speak the exact "
            "language of a job posting. You mirror JD keywords verbatim, rewrite bullets "
            "to quantify achievements, and reorder sections for maximum impact. "
            "Golden rules: NEVER invent experience. ALWAYS use exact JD keywords. "
            "ALWAYS start bullets with action verbs. ALWAYS quantify. NO personal pronouns. "
            "You operate in a human-in-the-loop system. Be accurate."
        ),
        verbose=True, llm=llm, tools=[GenerateResumePDFTool()],
    )

    cover_letter_manager = Agent(
        role="Professional cover letter writer specialising in ATS-optimised letters",
        goal="Write an eye-catching cover letter based on the TAILORED resume. Only if fit_score > 83.",
        backstory=(
            "You write with clarity and precision. You use the tailored resume — never the original. "
            "This letter will NOT be sent automatically. A human will approve it. "
            "Write honestly. Follow all rules in the prompt."
        ),
        verbose=True, llm=llm, tools=[GenerateCoverLetterPDFTool()],
    )

    pick_task = Task(
        description=f"""
{enriched_prompt}

Analyse these resumes: {{resumes}}

Choose the best resume for: {{job_description}}
Target: {{company_name}} — {{job_role}}

Score 0-100 honestly. Above 83 triggers tailoring and cover letter.
Return JSON with: reasoning, fit_score, skills_to_learn, best_resume_name, resume_text (full text of chosen resume).
""",
        expected_output="JSON: reasoning, fit_score, skills_to_learn, best_resume_name, resume_text",
        agent=resume_manager,
    )

    tailor_task = Task(
        description=f"""
{enriched_prompt}

Using the chosen resume from the previous task, rewrite it for:
Company: {{company_name}}
Role: {{job_role}}
Date: {{today}}

RULES:
- Keep all personal details exactly: Prasad Watane, chetanwattane@gmail.com, +49 15210901787, Heidelberg Germany
- Mirror exact JD keywords verbatim in Professional Summary and bullets
- Rewrite every bullet: action verb + quantified achievement
- No personal pronouns (I, me, my, we)
- No em dashes
- NEVER invent experience not in the original resume
- Add skills_to_learn as "Currently developing:" in Technical Skills
- Structure: Professional Summary → Work Experience → Technical Skills → Education → Publications → Projects

Save as: Prasad_Watane_{{company_name}}_{{job_role}}.pdf (spaces as underscores)
""",
        expected_output="JSON: tailored_resume_path, tailoring_notes, fit_score, skills_to_learn, best_resume_name",
        agent=resume_tailor,
        context=[pick_task],
    )

    cl_task = Task(
        description=f"""
{enriched_prompt}

Using the TAILORED resume from the previous task, write a cover letter for:
Company: {{company_name}} | Role: {{job_role}} | Date: {{today}}

Only generate if fit_score > 83. Otherwise set cover_letter_path to "no_cover_letter_generated".

LAYOUT (mandatory):
Line 1: Prasad Watane
Line 2: chetanwattane@gmail.com
Line 3: +49 15210901787
Line 4: {{today}}
[blank line]
Dear Hiring Team,

Opening: Catchy unique hook naming Prasad, M.Sc. Applied Data Science and Analytics,
SRH Hochschule Heidelberg, confirms studies complete. Never "I am writing to apply..."

Bold items (7-9 total covering A-E):
A. **SRH Hochschule Heidelberg** and **M.Sc. Applied Data Science and Analytics**
B. Two technical skills matching JD
C. One past employer + city + result (Onwards Technologies Pune or AGDATA India Pune)
D. Soft skill (**curious-minded** or **continuous learning**)
E. Hard-skill framework (**PyTorch** or **FastAPI** or **scikit-learn**)

Body 200-300 words: Intro 50w, Company para 70w, Middle 120w, Conclusion 60w
Close: Sincerely, Prasad Watane
No em dashes anywhere.

Save as: {{company_name}} - {{job_role}}.pdf
""",
        expected_output="JSON: cover_letter_path, reasoning, fit_score, skills_to_learn, best_resume_name, tailored_resume_path, tailoring_notes",
        agent=cover_letter_manager,
        context=[pick_task, tailor_task],
    )

    return Crew(
        agents=[resume_manager, resume_tailor, cover_letter_manager],
        tasks=[pick_task, tailor_task, cl_task],
        process=Process.sequential,
        verbose=True,
    )


def run_crew_for_single_job(application: dict, all_resumes_text: str,
                             redo_instruction: Optional[str] = None) -> dict:
    engine = CommandEngine()
    enriched_prompt = (
        engine.build_redo_prompt("", redo_instruction,
            application.get("company_name"), application.get("role"))
        if redo_instruction else
        engine.build_enriched_prompt(application.get("description", ""),
            application.get("company_name"), application.get("role"))
    )

    inputs = {
        "resumes":         all_resumes_text,
        "today":           datetime.now().strftime("%Y-%m-%d"),
        "job_description": application["description"],
        "company_name":    application["company_name"],
        "job_role":        application["role"],
    }

    print(f"Running 3-agent crew: {application['company_name']} — {application['role']}")
    the_crew = _make_crew(enriched_prompt)
    result = _parse(the_crew.kickoff(inputs=inputs))

    fit_score = float(result.get("fit_score", 0))
    ats_report = score_and_report(application["description"], all_resumes_text)
    if not ats_report.passed:
        notify_ats_warning(0, application["company_name"], application["role"],
                           ats_report.overall_score, ats_report.suggestions)

    record = ApplicationRecord(
        company_name=application["company_name"], job_role=application["role"],
        url=application.get("url"), job_description=application.get("description"),
        fit_score=fit_score, best_resume_name=result.get("best_resume_name", ""),
        skills_to_learn=result.get("skills_to_learn", []),
        reasoning=result.get("reasoning", ""),
        cover_letter_path=result.get("cover_letter_path", ""),
        ats_score=ats_report.overall_score, ats_report=ats_report.to_dict(),
        status="pending_review", source=application.get("source", "text"),
    )
    log_id = insert_application(record)
    print(f"Saved (id={log_id})")

    try:
        PushNotificationTool()._run(
            message=f"[Job Assistant] Review #{log_id}\n"
                    f"{application['company_name']} — {application['role']}\n"
                    f"Fit: {fit_score:.0f}/100\nTailored resume + cover letter ready")
    except Exception:
        pass
    notify_review_ready(log_id, application["company_name"], application["role"], fit_score)

    return {
        "log_id": log_id, "status": "pending_review",
        "company_name": application["company_name"], "job_role": application["role"],
        "fit_score": fit_score, "ats_score": round(ats_report.overall_score, 1),
        "best_resume_name": result.get("best_resume_name", ""),
        "cover_letter_path": result.get("cover_letter_path", ""),
        "tailored_resume_path": result.get("tailored_resume_path", ""),
        "skills_to_learn": result.get("skills_to_learn", []),
        "reasoning": result.get("reasoning", ""),
        "tailoring_notes": result.get("tailoring_notes", ""),
        "ats_report": ats_report.to_dict(),
        "message": f"Saved as pending_review (id={log_id}). Review at /review/pending",
    }


async def run_job_application_crew(company_name, job_role, job_description, url,
                                    all_resumes_text, redo_instruction=None) -> dict:
    app = {"company_name": company_name, "role": job_role,
           "description": job_description, "url": url}
    return await asyncio.get_event_loop().run_in_executor(
        None, run_crew_for_single_job, app, all_resumes_text, redo_instruction)


def _parse(raw) -> dict:
    if hasattr(raw, "model_dump"): return raw.model_dump()
    if isinstance(raw, dict): return raw
    text = getattr(raw, "raw", None) or str(raw)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(0))
        except: pass
    return {}
