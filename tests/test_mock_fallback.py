"""The local model is the first option; fixtures answer only after it fails."""

import pytest

from app.llm import StructuredOutputError
from app.mocks import (
    MOCK_MARKER,
    FallbackPDFParser,
    FallbackStructuredGenerator,
    MockStructuredGenerator,
)
from app.schemas import CandidateAssessment, CandidateProfile, JobCriterion, JobProfile
from app.talent import _simulated_content_warning


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
    fallback = MockStructuredGenerator()
    generator = FallbackStructuredGenerator(primary, fallback)

    result = await generator.generate(JobProfile, "system", "Analista de dados")

    assert result is REAL_PROFILE
    assert MOCK_MARKER not in result.title
    assert primary.calls == 1


@pytest.mark.asyncio
async def test_fixtures_answer_only_after_the_model_fails() -> None:
    primary = _Primary(error=StructuredOutputError("modelo indisponível"))
    generator = FallbackStructuredGenerator(primary)

    result = await generator.generate(JobProfile, "system", "Python; SQL")

    assert primary.calls == 1
    assert MOCK_MARKER in result.title
    assert result.is_actionable
    assert result.criteria, "an actionable profile needs at least one criterion"


@pytest.mark.asyncio
async def test_health_reports_the_real_model_state_not_the_fallback() -> None:
    assert await FallbackStructuredGenerator(_Primary(result=REAL_PROFILE)).healthy() is True
    assert await FallbackStructuredGenerator(_Primary(error=RuntimeError())).healthy() is False


@pytest.mark.asyncio
async def test_simulated_assessment_never_reports_supported() -> None:
    payload = (
        '{"job_profile": {"criteria": [{"id": "c1"}, {"id": "c2"}]}, "candidate_profile": {}}'
    )
    assessment = await MockStructuredGenerator().generate(CandidateAssessment, "system", payload)

    assert [item.criterion_id for item in assessment.criteria] == ["c1", "c2"]
    assert {item.status for item in assessment.criteria} == {"unclear"}
    assert MOCK_MARKER in (assessment.professional_summary or "")


@pytest.mark.asyncio
async def test_simulated_profile_carries_no_data_from_the_resume() -> None:
    resume = "Fulano de Tal\nEmpresa Confidencial LTDA\nPython avancado"
    profile = await MockStructuredGenerator().generate(CandidateProfile, "system", resume)

    rendered = profile.model_dump_json()
    assert "Confidencial" not in rendered
    assert "Fulano" not in rendered
    assert MOCK_MARKER in rendered


@pytest.mark.asyncio
async def test_pdf_parser_prefers_the_real_parser() -> None:
    expected = CandidateProfile(full_name="Perfil real")

    class _RealParser:
        def __init__(self) -> None:
            self.calls = 0

        async def parse(self, pdf_bytes: bytes) -> CandidateProfile:
            del pdf_bytes
            self.calls += 1
            return expected

    real = _RealParser()
    assert await FallbackPDFParser(real).parse(b"%PDF-1.4") is expected
    assert real.calls == 1


@pytest.mark.asyncio
async def test_pdf_parser_falls_back_to_fixtures_after_failure() -> None:
    class _BrokenParser:
        async def parse(self, pdf_bytes: bytes) -> CandidateProfile:
            del pdf_bytes
            raise StructuredOutputError("gemini indisponível")

    profile = await FallbackPDFParser(_BrokenParser()).parse(b"%PDF-1.4")

    assert MOCK_MARKER in profile.model_dump_json()


def test_warning_is_added_only_when_content_is_simulated() -> None:
    clean = CandidateAssessment(candidate_id="a", professional_summary="Resumo real")
    simulated = CandidateAssessment(candidate_id="b", professional_summary=f"{MOCK_MARKER} Resumo")

    assert _simulated_content_warning(REAL_PROFILE, [clean]) is None
    assert MOCK_MARKER in (_simulated_content_warning(REAL_PROFILE, [simulated]) or "")
    mocked_profile = REAL_PROFILE.model_copy(update={"title": f"{MOCK_MARKER} Vaga"})
    assert _simulated_content_warning(mocked_profile, [clean]) is not None
