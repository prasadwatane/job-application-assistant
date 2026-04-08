"""
ATS Optimizer Service
======================
Post-processes Claude's resume/cover letter output to:
  1. Score keyword coverage against the job description
  2. Flag missing high-priority keywords
  3. Validate bullet formatting (action verb, length, quantification)
  4. Validate cover letter length and structure
  5. Return a structured report + improved text where auto-fixable

This runs AFTER the Claude agents produce output and BEFORE saving to PDF.
A low ATS score (< 60) triggers a warning in the job log and notification.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

# ── Strong action verbs whitelist ──────────────────────────────────────────

ACTION_VERBS = {
    "accelerated", "achieved", "administered", "advanced", "architected",
    "automated", "built", "championed", "coached", "collaborated",
    "consolidated", "contributed", "created", "cut", "delivered",
    "deployed", "designed", "developed", "devised", "directed",
    "drove", "enabled", "engineered", "enhanced", "established",
    "executed", "expanded", "generated", "grew", "guided",
    "identified", "implemented", "improved", "increased", "integrated",
    "introduced", "launched", "led", "managed", "mentored",
    "migrated", "modelled", "modernised", "optimised", "orchestrated",
    "owned", "partnered", "pioneered", "planned", "produced",
    "published", "rebuilt", "redesigned", "reduced", "refactored",
    "released", "replaced", "researched", "resolved", "restructured",
    "scaled", "secured", "shipped", "simplified", "spearheaded",
    "streamlined", "tested", "transformed", "unified", "upgraded",
}

# Patterns that indicate quantification
_QUANTITY_RE = re.compile(
    r"(\d+[\.,]?\d*\s*(%|percent|x|times|hrs?|hours?|days?|weeks?|months?|years?|k|m|bn|"
    r"users?|customers?|requests?|services?|engineers?|team members?|repos?))|"
    r"(\$\s*\d+)",
    re.IGNORECASE,
)


# ── Scoring output dataclass ───────────────────────────────────────────────

@dataclass
class ATSReport:
    keyword_score: float = 0.0          # 0–100
    bullet_score: float = 0.0           # 0–100
    overall_score: float = 0.0          # weighted average
    missing_keywords: list[str] = field(default_factory=list)
    weak_bullets: list[str] = field(default_factory=list)   # snippets needing work
    cover_letter_issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    passed: bool = False                # True if overall_score >= 60

    def to_dict(self) -> dict:
        return {
            "keyword_score":         round(self.keyword_score, 1),
            "bullet_score":          round(self.bullet_score, 1),
            "overall_score":         round(self.overall_score, 1),
            "passed":                self.passed,
            "missing_keywords":      self.missing_keywords,
            "weak_bullets":          self.weak_bullets[:5],   # top 5 only
            "cover_letter_issues":   self.cover_letter_issues,
            "suggestions":           self.suggestions,
        }


# ── Keyword extraction ─────────────────────────────────────────────────────

def _extract_keywords(text: str, min_len: int = 4) -> list[str]:
    """
    Extract meaningful keywords from a job description.
    Returns lowercase deduplicated list, ordered by first occurrence.
    """
    # strip HTML tags if any
    clean = re.sub(r"<[^>]+>", " ", text)
    # tokenise on word boundaries
    tokens = re.findall(r"\b[a-zA-Z][a-zA-Z0-9#+.\-]{" + str(min_len - 1) + r",}\b", clean)
    # normalise
    seen: set[str] = set()
    result: list[str] = []
    stopwords = {
        "with", "that", "this", "have", "from", "will", "your", "they",
        "been", "their", "more", "about", "also", "such", "into", "than",
        "when", "what", "which", "where", "some", "team", "role", "work",
        "able", "well", "must", "like", "need", "both", "each", "other",
        "very", "good", "great", "strong", "using", "help", "make",
        "over", "would", "could", "should", "including", "required",
        "experience", "skills", "knowledge", "ability", "within",
        "across", "through", "ensure", "working", "looking",
    }
    for t in tokens:
        lower = t.lower()
        if lower not in stopwords and lower not in seen:
            seen.add(lower)
            result.append(lower)
    return result


# ── Bullet analysis ────────────────────────────────────────────────────────

def _analyse_bullet(bullet: str) -> tuple[bool, list[str]]:
    """
    Check a single resume bullet.
    Returns (is_strong: bool, issues: list[str]).
    """
    issues: list[str] = []
    b = bullet.strip().lstrip("-•◦▪▸ ").strip()
    if not b:
        return True, []

    # starts with action verb?
    first_word = b.split()[0].lower().rstrip(".,;:") if b.split() else ""
    if first_word not in ACTION_VERBS:
        issues.append(f"Weak opening '{first_word}' — use a strong action verb")

    # too long?
    word_count = len(b.split())
    if word_count > 35:
        issues.append(f"Too long ({word_count} words) — aim for ≤ 25 words")

    # has personal pronoun?
    if re.search(r"\b(i|me|my|we|our)\b", b, re.IGNORECASE):
        issues.append("Contains personal pronoun — remove I/me/my/we/our")

    # quantified?
    if not _QUANTITY_RE.search(b):
        issues.append("No metric — add a number, %, or measurable outcome")

    return len(issues) == 0, issues


# ── Cover letter analysis ──────────────────────────────────────────────────

def _analyse_cover_letter(text: str) -> list[str]:
    issues: list[str] = []
    word_count = len(text.split())

    if word_count < 200:
        issues.append(f"Too short ({word_count} words) — target 250–350 words")
    elif word_count > 400:
        issues.append(f"Too long ({word_count} words) — trim to under 350 words")

    first_line = text.strip().split("\n")[0].lower()
    filler_openers = [
        "i am writing", "i would like to", "please find", "attached is",
        "i am applying", "i am interested",
    ]
    if any(f in first_line for f in filler_openers):
        issues.append("Generic opener detected — start with a compelling hook instead")

    if not re.search(r"\d", text):
        issues.append("No numbers or metrics found — add at least one quantified achievement")

    return issues


# ── Main scorer ────────────────────────────────────────────────────────────

def score_and_report(
    job_description: str,
    resume_text: str,
    cover_letter_text: Optional[str] = None,
) -> ATSReport:
    """
    Score resume (and optionally cover letter) against a job description.

    Parameters
    ----------
    job_description   : Raw JD text
    resume_text       : The Claude-generated resume content
    cover_letter_text : The Claude-generated cover letter (optional)

    Returns
    -------
    ATSReport with scores, missing keywords, bullet issues, and suggestions.
    """
    report = ATSReport()

    # ── 1. Keyword coverage ───────────────────────────────────────────────
    jd_keywords = _extract_keywords(job_description)
    # weight the first 30 keywords more (they appear earlier = more important)
    priority_kws = set(jd_keywords[:30])
    all_kws = set(jd_keywords)
    resume_lower = resume_text.lower()

    present = sum(1 for kw in all_kws if kw in resume_lower)
    total = max(len(all_kws), 1)
    report.keyword_score = min((present / total) * 100, 100)

    report.missing_keywords = [
        kw for kw in jd_keywords[:40]      # top 40 JD keywords only
        if kw not in resume_lower
    ][:15]                                  # surface top 15 missing

    # ── 2. Bullet quality ─────────────────────────────────────────────────
    bullet_lines = [
        line for line in resume_text.split("\n")
        if line.strip().startswith(("-", "•", "◦", "▪", "▸", "*"))
           or (len(line.strip()) > 10 and not line.strip().endswith(":"))
    ]

    if bullet_lines:
        strong_count = 0
        for b in bullet_lines:
            ok, issues = _analyse_bullet(b)
            if ok:
                strong_count += 1
            else:
                report.weak_bullets.append(b.strip()[:80])
        report.bullet_score = (strong_count / len(bullet_lines)) * 100
    else:
        report.bullet_score = 50.0  # no bullets to score

    # ── 3. Cover letter check ─────────────────────────────────────────────
    if cover_letter_text:
        report.cover_letter_issues = _analyse_cover_letter(cover_letter_text)
        cl_lower = cover_letter_text.lower()
        cl_missing = [kw for kw in jd_keywords[:20] if kw not in cl_lower][:8]
        if cl_missing:
            report.cover_letter_issues.append(
                f"Missing JD keywords in cover letter: {', '.join(cl_missing)}"
            )

    # ── 4. Overall score ──────────────────────────────────────────────────
    weights = {"keyword": 0.5, "bullet": 0.5}
    report.overall_score = (
        report.keyword_score * weights["keyword"]
        + report.bullet_score * weights["bullet"]
    )
    report.passed = report.overall_score >= 60

    # ── 5. Actionable suggestions ─────────────────────────────────────────
    if report.missing_keywords:
        top = report.missing_keywords[:5]
        report.suggestions.append(
            f"Add these missing keywords naturally: {', '.join(top)}"
        )
    if report.weak_bullets:
        report.suggestions.append(
            f"{len(report.weak_bullets)} bullet(s) need stronger action verbs or metrics"
        )
    if not report.passed:
        report.suggestions.append(
            "Overall ATS score < 60 — review missing keywords and bullet quality before submitting"
        )

    return report


def inject_missing_keywords(
    text: str,
    missing: list[str],
    section_hint: str = "Technical Skills",
) -> str:
    """
    Append missing keywords to a designated section of the resume text.
    Avoids keyword stuffing — only injects into a skills-type section.
    """
    if not missing:
        return text
    additions = ", ".join(missing[:10])
    marker = re.search(
        rf"({re.escape(section_hint)}[:\s])", text, re.IGNORECASE
    )
    if marker:
        insert_pos = marker.end()
        return text[:insert_pos] + f"\n  Additional: {additions}\n" + text[insert_pos:]
    return text + f"\n\n{section_hint}: {additions}"
