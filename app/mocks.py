"""Offline stand-ins for the local model, used when the LLM endpoint is down.

These implementations satisfy `StructuredGenerator` and `PDFFallback` so the
deterministic pipeline (Drive sync, report building, Sheets/XLSX creation) can be
exercised without the OpenAI-compatible server. They fabricate nothing that could
be mistaken for a real assessment: every text field carries `MOCK_MARKER` and no
criterion is ever reported as `supported`.
"""

import hashlib
import json
import logging
import re
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from app.schemas import (
    CandidateAssessment,
    CandidateProfile,
    CriterionAssessment,
    JobCriterion,
    JobProfile,
    ProfileEvidence,
)

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

MOCK_MARKER = "[DADOS SIMULADOS - NAO USAR PARA DECISAO]"
MAX_MOCK_CRITERIA = 5
_FRAGMENT = re.compile(r"[\n;.]+")


def _fragments(description: str) -> list[str]:
    """Turn the manager's description into criterion-sized fragments."""
    parts = [" ".join(part.split()) for part in _FRAGMENT.split(description)]
    return [part for part in parts if len(part) > 2][:MAX_MOCK_CRITERIA]


def _pseudonym(source: str) -> str:
    """Stable, obviously synthetic label so report rows stay distinguishable."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:4].upper()
    return f"Candidato Simulado {digest}"


def _mock_job_profile(description: str) -> JobProfile:
    fragments = _fragments(description) or ["Requisito profissional simulado"]
    criteria = [
        JobCriterion(
            id=f"mock_{index}",
            description=f"{MOCK_MARKER} {fragment}"[:500],
            required=index <= 2,
            criterion_type="other",
        )
        for index, fragment in enumerate(fragments, start=1)
    ]
    title = " ".join(description.split())[:80] or "Vaga simulada"
    return JobProfile(
        title=f"{MOCK_MARKER} {title}",
        summary=(
            f"{MOCK_MARKER} Perfil gerado sem o modelo local. Os critérios abaixo "
            "reproduzem o texto do pedido e não passaram por interpretação."
        ),
        criteria=criteria,
        is_actionable=True,
    )


def _mock_candidate_profile(resume_text: str) -> CandidateProfile:
    """Never derive real data from the resume; only a stable synthetic label."""
    return CandidateProfile(
        full_name=_pseudonym(resume_text),
        education=[f"{MOCK_MARKER} Formação não extraída"],
        languages=[f"{MOCK_MARKER} Idiomas não extraídos"],
        experiences=[f"{MOCK_MARKER} Experiência não extraída"],
        skills=[f"{MOCK_MARKER} Competências não extraídas"],
        certifications=[],
        evidence=[
            ProfileEvidence(
                field="skill",
                fact=f"{MOCK_MARKER} Nenhum fato profissional foi extraído",
                source_excerpt=f"{MOCK_MARKER} Sem trecho de origem",
            )
        ],
        relevant_experience_years=None,
    )


def _criterion_ids(payload: str) -> list[str]:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    criteria = (data or {}).get("job_profile", {}).get("criteria", [])
    if not isinstance(criteria, list):
        return []
    return [str(item["id"]) for item in criteria if isinstance(item, dict) and item.get("id")]


def _mock_assessment(payload: str) -> CandidateAssessment:
    """Report every criterion as `unclear`: simulated runs assert nothing."""
    note = f"{MOCK_MARKER} Avaliação não executada; o modelo local estava indisponível."
    return CandidateAssessment(
        candidate_id="",
        criteria=[
            CriterionAssessment(criterion_id=criterion_id, status="unclear", notes=note)
            for criterion_id in _criterion_ids(payload)
        ],
        points_to_confirm=[f"{MOCK_MARKER} Revisar todos os critérios manualmente."],
        professional_summary=(
            f"{MOCK_MARKER} Resumo não gerado. Execute novamente com o modelo local "
            "disponível para obter evidências reais."
        ),
    )


class MockStructuredGenerator:
    """`StructuredGenerator` that answers from fixtures instead of the network."""

    async def generate(self, schema: type[T], system_prompt: str, user_input: str) -> T:
        builders: dict[Any, Any] = {
            JobProfile: _mock_job_profile,
            CandidateProfile: _mock_candidate_profile,
            CandidateAssessment: _mock_assessment,
        }
        builder = builders.get(schema)
        if builder is None:
            raise NotImplementedError(f"No mock fixture for {schema.__name__}")
        return cast(T, builder(user_input))

    async def healthy(self) -> bool:
        """Keep `/health` green so the container does not report unhealthy."""
        return True


class MockPDFFallback:
    """`PDFFallback` used when a PDF has no usable extractable text."""

    async def parse(self, pdf_bytes: bytes) -> CandidateProfile:
        return _mock_candidate_profile(hashlib.sha256(pdf_bytes).hexdigest())


class FallbackStructuredGenerator:
    """Real generator first; fixtures only after it fails.

    Degradation is never silent: fixture output carries `MOCK_MARKER`, which the
    service turns into a search warning, and each fallback is logged with the
    schema name and error type only.
    """

    def __init__(
        self,
        primary: Any,
        fallback: Any | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback if fallback is not None else MockStructuredGenerator()

    async def generate(self, schema: type[T], system_prompt: str, user_input: str) -> T:
        try:
            return cast(T, await self.primary.generate(schema, system_prompt, user_input))
        except Exception as exc:
            _log_event(
                "structured_generation_mocked",
                schema=schema.__name__,
                status="degraded",
                error_type=type(exc).__name__,
            )
            return cast(T, await self.fallback.generate(schema, system_prompt, user_input))

    async def healthy(self) -> bool:
        """Report the real model's state so `/health` can show degradation."""
        probe = getattr(self.primary, "healthy", None)
        if probe is None:
            return True
        return bool(await probe())


class FallbackPDFParser:
    """Gemini first, fixtures only after it fails."""

    def __init__(self, primary: Any, fallback: Any | None = None) -> None:
        self.primary = primary
        self.fallback = fallback if fallback is not None else MockPDFFallback()

    async def parse(self, pdf_bytes: bytes) -> CandidateProfile:
        try:
            return cast(CandidateProfile, await self.primary.parse(pdf_bytes))
        except Exception as exc:
            _log_event(
                "pdf_parse_mocked",
                status="degraded",
                error_type=type(exc).__name__,
            )
            return await self.fallback.parse(pdf_bytes)


def _log_event(event: str, **fields: object) -> None:
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))
