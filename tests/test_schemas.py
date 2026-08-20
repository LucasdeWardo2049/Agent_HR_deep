import pytest
from pydantic import ValidationError

from app.schemas import CandidateProfile, JobCriterion, JobProfile, TalentSearchRequest


def test_candidate_profile_rejects_protected_attributes() -> None:
    with pytest.raises(ValidationError):
        CandidateProfile(full_name="Ana", age=38)  # type: ignore[call-arg]


def test_actionable_job_requires_criteria() -> None:
    with pytest.raises(ValidationError):
        JobProfile(title="Backend", criteria=[])

    profile = JobProfile(
        title="Backend",
        criteria=[
            JobCriterion(
                id="python",
                description="Experiência profissional com Python",
                required=True,
                criterion_type="skill",
            )
        ],
    )
    assert profile.is_actionable is True


def test_ambiguous_job_requires_a_question() -> None:
    with pytest.raises(ValidationError):
        JobProfile(title="Indefinida", is_actionable=False)


def test_search_request_has_a_small_bounded_payload() -> None:
    with pytest.raises(ValidationError):
        TalentSearchRequest(description="oi")
