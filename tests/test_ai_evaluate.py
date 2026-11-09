"""Gap rules and the evaluate_job flow, with a fake LLM (no API calls)."""
from jobalert.ai.evaluate import apply_gap_rules, evaluate_job, total_score
from jobalert.ai.llm import LLMResult
from jobalert.ai.profile import Profile

AREAS = {"people_management": 4.0, "software_development": 0.5, "data_business_analysis": 3.0}

PROFILE = Profile(
    yaml_text="""
experience_areas:
  people_management: {years: 4}
  software_development: {years: 0.5}
  data_business_analysis: {years: 3}
""",
    cv_text="CV text",
)


def exp(years, areas, must=True, he="תחום"):
    return {"text": "x", "category": "experience", "must_have": must, "experience_type": "x",
            "experience_type_he": he, "years_required": years, "matched_areas": areas, "met": False}


def test_requirements_met_is_alert():
    d = apply_gap_rules([exp(4, ["people_management"])], False, AREAS)   # "4+ years managing a team"
    assert d.decision == "alert" and not d.gaps


def test_gap_up_to_four_years_is_borderline():
    d = apply_gap_rules([exp(4, ["software_development"])], False, AREAS)  # 4 - 0.5 = 3.5
    assert d.decision == "borderline"
    assert d.gaps[0].gap == 3.5


def test_gap_of_four_and_a_half_is_still_borderline():
    d = apply_gap_rules([exp(5, [])], False, AREAS)   # 5 - 0 = 5 -> reject
    assert d.decision == "reject" and d.reject_reason.startswith("gap>=")
    d = apply_gap_rules([exp(5, ["software_development"])], False, AREAS)  # 4.5 -> borderline
    assert d.decision == "borderline"


def test_best_matching_area_counts():
    d = apply_gap_rules([exp(3, ["software_development", "data_business_analysis"])], False, AREAS)
    assert d.decision == "alert"


def test_nice_to_have_and_unnumbered_requirements_never_create_gaps():
    reqs = [exp(10, [], must=False), exp(None, []), {"category": "skill", "must_have": True, "text": "SQL"}]
    assert apply_gap_rules(reqs, False, AREAS).decision == "alert"


def test_enrollment_required_rejects_but_student_title_alone_does_not():
    assert apply_gap_rules([], True, AREAS).reject_reason == "enrollment_required"
    assert apply_gap_rules([], False, AREAS).decision == "alert"


def test_unknown_area_names_are_ignored():
    d = apply_gap_rules([exp(2, ["made_up_area"])], False, AREAS)
    assert d.decision == "borderline" and d.gaps[0].cv_years == 0


def test_total_score_clamps_each_part():
    score, rubric = total_score({"must_have": 55, "experience": -3, "tools": 20, "advantages": 7})
    assert rubric == {"must_have": 40, "experience": 0, "tools": 20, "advantages": 7}
    assert score == 67


class FakeLLM:
    def __init__(self, filter_data, score_data=None):
        self.responses = [filter_data, score_data]
        self.calls = []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        data = self.responses[len(self.calls) - 1]
        if data == "refused":
            return LLMResult(None, refused=True, model=kwargs["model"])
        return LLMResult(data, model=kwargs["model"], usage={"input": 1, "output": 1})


AI = {"filter_model": "haiku", "score_model": "sonnet"}
JOB = {"id": 1, "company": "Acme", "title": "Team Lead", "location": "Tel Aviv", "description": "..."}


def test_reject_skips_the_scoring_call():
    llm = FakeLLM({"requirements": [exp(6, [])], "enrollment_required": False, "enrollment_evidence": ""})
    ev = evaluate_job(llm, PROFILE, JOB, AI)
    assert ev.decision == "reject" and len(llm.calls) == 1


def test_borderline_is_scored_with_hebrew_gaps():
    llm = FakeLLM(
        {"requirements": [exp(2, ["software_development"], he="פיתוח תוכנה")], "enrollment_required": False,
         "enrollment_evidence": ""},
        {"must_have": 30, "experience": 15, "tools": 15, "advantages": 5, "reason_he": "מתאים חלקית"},
    )
    ev = evaluate_job(llm, PROFILE, JOB, AI)
    assert ev.decision == "borderline" and ev.score == 65
    assert ev.gaps_he == "פיתוח תוכנה: נדרש 2, יש 0.5"
    assert llm.calls[1]["use_fallback"] is True                  # Sonnet call opts into fallback
    assert "requirement_analysis" in llm.calls[1]["user_text"]
    assert llm.calls[0]["schema"]["properties"]["requirements"]["items"]["properties"]["matched_areas"][
        "items"]["enum"] == sorted(AREAS)


def test_unreadable_filter_output_still_alerts_without_score():
    ev = evaluate_job(FakeLLM("refused"), PROFILE, JOB, AI)
    assert ev.decision == "alert" and ev.score is None and ev.reason_he
