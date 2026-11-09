"""Evaluate one job: Haiku extracts requirements, code applies the gap rules, Sonnet scores.

Gap rules (fixed, in code so they are consistent):
- the posting requires active student enrollment  -> reject
- largest must-have experience gap >= 5 years     -> reject
- largest gap > 0                                 -> borderline (alert, with what's missing)
- no gap                                          -> alert
Job titles are never used to filter.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from .llm import LLM, LLMResult
from .profile import Profile
from .prompts import FILTER_INSTRUCTIONS, SCORE_INSTRUCTIONS

log = logging.getLogger(__name__)

REJECT_GAP_YEARS = 5.0
RUBRIC_MAX = {"must_have": 40, "experience": 25, "tools": 20, "advantages": 15}
UNPARSED_NOTE_HE = "לא הצלחתי לנתח את המשרה אוטומטית, כדאי להציץ בה בעצמך."


@dataclass
class Gap:
    experience_type: str
    experience_type_he: str
    years_required: float
    cv_years: float
    gap: float


@dataclass
class FilterDecision:
    decision: str                      # alert | borderline | reject
    reject_reason: str | None
    gaps: list[Gap]
    requirements: list[dict[str, Any]]


@dataclass
class Evaluation:
    decision: str
    reject_reason: str | None
    requirements: list[dict[str, Any]]
    gaps: list[Gap]
    score: int | None
    rubric: dict[str, int] | None
    reason_he: str | None
    models: dict[str, Any] = field(default_factory=dict)

    @property
    def borderline(self) -> bool:
        return self.decision == "borderline"

    @property
    def gaps_he(self) -> str | None:
        if not self.gaps:
            return None
        return "; ".join(
            f"{g.experience_type_he}: נדרש {_num(g.years_required)}, יש {_num(g.cv_years)}" for g in self.gaps
        )


def _num(x: float) -> str:
    return f"{x:g}"


# ------------------------------------------------------------------ schemas

def filter_schema(area_keys: list[str]) -> dict[str, Any]:
    requirement = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "category": {"type": "string", "enum": ["experience", "education", "skill", "language", "other"]},
            "must_have": {"type": "boolean"},
            "experience_type": {"type": "string"},
            "experience_type_he": {"type": "string"},
            "years_required": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "matched_areas": {"type": "array", "items": {"type": "string", "enum": area_keys}},
            "met": {"type": "boolean"},
        },
        "required": ["text", "category", "must_have", "experience_type", "experience_type_he",
                     "years_required", "matched_areas", "met"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "requirements": {"type": "array", "items": requirement},
            "enrollment_required": {"type": "boolean"},
            "enrollment_evidence": {"type": "string"},
        },
        "required": ["requirements", "enrollment_required", "enrollment_evidence"],
        "additionalProperties": False,
    }


SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "must_have": {"type": "integer"},
        "experience": {"type": "integer"},
        "tools": {"type": "integer"},
        "advantages": {"type": "integer"},
        "reason_he": {"type": "string"},
    },
    "required": ["must_have", "experience", "tools", "advantages", "reason_he"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ gap rules (pure)

def apply_gap_rules(
    requirements: list[dict[str, Any]], enrollment_required: bool, area_years: dict[str, float]
) -> FilterDecision:
    annotated: list[dict[str, Any]] = []
    gaps: list[Gap] = []
    for req in requirements:
        req = dict(req)
        years = req.get("years_required")
        if req.get("category") == "experience" and req.get("must_have") and isinstance(years, (int, float)):
            matched = [a for a in req.get("matched_areas") or [] if a in area_years]
            cv_years = max((area_years[a] for a in matched), default=0.0)
            gap = max(0.0, float(years) - cv_years)
            req["cv_years"], req["gap"] = cv_years, gap
            if gap > 0:
                gaps.append(Gap(req.get("experience_type", ""), req.get("experience_type_he", ""),
                                float(years), cv_years, gap))
        annotated.append(req)

    max_gap = max((g.gap for g in gaps), default=0.0)
    if enrollment_required:
        return FilterDecision("reject", "enrollment_required", gaps, annotated)
    if max_gap >= REJECT_GAP_YEARS:
        return FilterDecision("reject", f"gap>={REJECT_GAP_YEARS:g}y", gaps, annotated)
    if max_gap > 0:
        return FilterDecision("borderline", None, gaps, annotated)
    return FilterDecision("alert", None, gaps, annotated)


def total_score(rubric: dict[str, Any]) -> tuple[int, dict[str, int]]:
    clamped = {k: max(0, min(int(rubric.get(k, 0) or 0), cap)) for k, cap in RUBRIC_MAX.items()}
    return sum(clamped.values()), clamped


# ------------------------------------------------------------------ orchestration

def job_text(job: dict[str, Any], max_chars: int) -> str:
    description = (job.get("description") or "").strip() or "(no description available)"
    if len(description) > max_chars:
        log.warning("Description of job %s is %d chars; using the first %d", job.get("id"), len(description), max_chars)
        description = description[:max_chars]
    return (
        "<job>\n"
        f"Company: {job.get('company')}\nTitle: {job.get('title')}\nLocation: {job.get('location') or 'n/a'}\n\n"
        f"{description}\n</job>"
    )


def evaluate_job(llm: LLM, profile: Profile, job: dict[str, Any], ai_cfg: dict[str, Any]) -> Evaluation:
    """Raises TransientAIError on network/rate-limit problems (caller retries next run)."""
    text = job_text(job, int(ai_cfg.get("max_description_chars", 60000)))
    profile_block = profile.prompt_block()
    models: dict[str, Any] = {}

    filtered: LLMResult = llm.structured(
        model=ai_cfg["filter_model"],
        system_blocks=[FILTER_INSTRUCTIONS, profile_block],
        user_text=text,
        schema=filter_schema(sorted(profile.area_years)),
        effort=ai_cfg.get("filter_effort", "medium"),
    )
    models["filter"] = {"model": filtered.model, **filtered.usage}
    if filtered.data is None:
        # Declined or unparseable: don't silently drop the job; alert without a score.
        return Evaluation("alert", None, [], [], None, None, UNPARSED_NOTE_HE, models)

    decision = apply_gap_rules(
        filtered.data.get("requirements") or [],
        bool(filtered.data.get("enrollment_required")),
        profile.area_years,
    )
    if decision.decision == "reject":
        return Evaluation("reject", decision.reject_reason, decision.requirements, decision.gaps,
                          None, None, None, models)

    analysis = {
        "requirements": decision.requirements,
        "experience_gaps": [asdict(g) for g in decision.gaps],
    }
    scored: LLMResult = llm.structured(
        model=ai_cfg["score_model"],
        system_blocks=[SCORE_INSTRUCTIONS, profile_block],
        user_text=text + "\n\n<requirement_analysis>\n" + json.dumps(analysis, ensure_ascii=False) + "\n</requirement_analysis>",
        schema=SCORE_SCHEMA,
        effort=ai_cfg.get("score_effort", "medium"),
        use_fallback=True,
    )
    models["score"] = {"model": scored.model, **scored.usage}
    if scored.data is None:
        return Evaluation(decision.decision, None, decision.requirements, decision.gaps,
                          None, None, UNPARSED_NOTE_HE, models)
    score, rubric = total_score(scored.data)
    return Evaluation(decision.decision, None, decision.requirements, decision.gaps,
                      score, rubric, (scored.data.get("reason_he") or "").strip() or None, models)
