"""The local model is the first option; sample fixtures answer only after it fails."""

import pytest

from app.fixtures import (
    SAMPLE_MARKER,
    FallbackPDFParser,
    FallbackStructuredGenerator,
    SampleStructuredGenerator,
)
from app.llm import StructuredOutputError
from app.schemas import CandidateAssessment, CandidateProfile, JobCriterion, JobProfile
from app.talent import (
    _simulated_content_warning,
    assessment_cache_key,
    is_sample_content,
    job_profile_cache_key,
)


class _Primary:
    """Structured generator that either answers or raises, and counts calls."""

    def __init__(self, result: object | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.healthy_result = error is None

    async def generate(self, schema: type, system_prompt: str, user_input: str) -> object:
        del schema, system_prompt, user_input
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    async def healthy(self) -> bool:
        return self.healthy_result


REAL_PROFILE = JobProfile(
    title="Analista de dados",
    criteria=[JobCriterion(id="c1", description="Python", required=True, criterion_type="skill")],
)


@pytest.mark.asyncio
async def test_primary_result_is_used_and_fixtures_are_not_touched() -> None:
    primary = _Primary(result=REAL_PROFILE)
    generator = FallbackStructuredGenerator(primary, SampleStructuredGenerator())

    result = await generator.generate(JobProfile, "system", "Analista de dados")

    assert result is REAL_PROFILE
    assert SAMPLE_MARKER not in result.title
    assert primary.calls == 1


@pytest.mark.asyncio
async def test_fixtures_answer_only_after_the_model_fails() -> None:
    primary = _Primary(error=StructuredOutputError("modelo indisponível"))
    generator = FallbackStructuredGenerator(primary)

    result = await generator.generate(JobProfile, "system", "Python; SQL")

    assert primary.calls == 1
    assert SAMPLE_MARKER in result.title
    assert result.is_actionable
    assert result.criteria, "an actionable profile needs at least one criterion"


@pytest.mark.asyncio
async def test_health_reports_the_real_model_state_not_the_fallback() -> None:
    assert await FallbackStructuredGenerator(_Primary(result=REAL_PROFILE)).healthy() is True
    assert await FallbackStructuredGenerator(_Primary(error=RuntimeError())).healthy() is False


@pytest.mark.asyncio
async def test_sample_assessment_never_reports_supported() -> None:
    payload = '{"job_profile": {"criteria": [{"id": "c1"}, {"id": "c2"}]}, "candidate_profile": {}}'
    assessment = await SampleStructuredGenerator().generate(CandidateAssessment, "system", payload)

    assert [item.criterion_id for item in assessment.criteria] == ["c1", "c2"]
    assert {item.status for item in assessment.criteria} == {"unclear"}
    assert SAMPLE_MARKER in (assessment.professional_summary or "")


@pytest.mark.asyncio
async def test_sample_profile_carries_no_data_from_the_resume() -> None:
    resume = "Fulano de Tal\nEmpresa Confidencial LTDA\nPython avancado"
    profile = await SampleStructuredGenerator().generate(CandidateProfile, "system", resume)

    rendered = profile.model_dump_json()
    assert "Confidencial" not in rendered
    assert "Fulano" not in rendered
    assert SAMPLE_MARKER in rendered


@pytest.mark.asyncio
async def test_pdf_parser_falls_back_to_fixtures_only_after_failure() -> None:
    expected = CandidateProfile(full_name="Perfil real")

    class _RealParser:
        async def parse(self, pdf_bytes: bytes) -> CandidateProfile:
            del pdf_bytes
            return expected

    class _BrokenParser:
        async def parse(self, pdf_bytes: bytes) -> CandidateProfile:
            del pdf_bytes
            raise StructuredOutputError("gemini indisponível")

    assert await FallbackPDFParser(_RealParser()).parse(b"%PDF-1.4") is expected
    assert SAMPLE_MARKER in (await FallbackPDFParser(_BrokenParser()).parse(b"%PDF-1.4")).model_dump_json()


def test_warning_is_added_only_when_content_is_a_sample() -> None:
    clean = CandidateAssessment(candidate_id="a", professional_summary="Resumo real")
    sampled = CandidateAssessment(candidate_id="b", professional_summary=f"{SAMPLE_MARKER}. Resumo")

    assert _simulated_content_warning(REAL_PROFILE, [clean]) is None
    assert _simulated_content_warning(REAL_PROFILE, [sampled]) == "Resultado gerado com dados de amostra."
    sampled_profile = REAL_PROFILE.model_copy(update={"title": f"{SAMPLE_MARKER} — Vaga"})
    assert _simulated_content_warning(sampled_profile, [clean]) is not None


def test_sample_content_is_detected_so_it_is_never_cached() -> None:
    assert is_sample_content("Resumo real") is False
    assert is_sample_content(None, "Resumo real") is False
    assert is_sample_content(f"{SAMPLE_MARKER}. Resumo") is True


def test_job_profile_cache_key_ignores_irrelevant_whitespace() -> None:
    assert job_profile_cache_key("Analista  de   dados") == job_profile_cache_key("Analista de dados")
    assert job_profile_cache_key("Analista de dados") != job_profile_cache_key("Engenheiro de dados")


def test_assessment_cache_key_tracks_both_inputs() -> None:
    candidate = CandidateProfile(candidate_id="x", skills=["Python"])
    other_candidate = CandidateProfile(candidate_id="x", skills=["SQL"])
    other_job = REAL_PROFILE.model_copy(update={"title": "Engenheiro de dados"})

    base = assessment_cache_key(REAL_PROFILE, candidate)

    assert base == assessment_cache_key(REAL_PROFILE, candidate)
    assert base != assessment_cache_key(other_job, candidate)
    assert base != assessment_cache_key(REAL_PROFILE, other_candidate)


def test_assessment_cache_key_ignores_candidate_identity() -> None:
    """The key must depend on professional evidence, not on the row id."""
    first = CandidateProfile(candidate_id="candidate_1", skills=["Python"])
    second = CandidateProfile(candidate_id="candidate_2", skills=["Python"])

    assert assessment_cache_key(REAL_PROFILE, first) == assessment_cache_key(REAL_PROFILE, second)
