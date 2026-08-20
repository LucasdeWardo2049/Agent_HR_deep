import pymupdf

from app.google_workspace import DOCX_MIME, PDF_MIME
from app.schemas import (
    CandidateAssessment,
    CandidateProfile,
    CriterionAssessment,
    JobCriterion,
    JobProfile,
    ResumeFile,
)
from app.settings import Settings
from app.talent import (
    TalentSearchProgressEvent,
    TalentService,
    extract_pdf_text,
    is_extraction_usable,
    normalize_assessment,
    profile_cache_hash,
)
from tests.fakes import FakeFallback, FakeStore, FakeWorkspace, QueueGenerator


def _pdf_with_text(text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(page.rect + (36, 36, -36, -36), text, fontsize=10)
    content = document.tobytes()
    document.close()
    return content


def _settings() -> Settings:
    return Settings(_env_file=None, local_llm_api_key="test-key")


async def test_unchanged_file_is_not_processed() -> None:
    content = b"unchanged document"
    file = ResumeFile(drive_file_id="cv-1", file_name="ana.docx", mime_type=DOCX_MIME)
    store = FakeStore()
    store.hashes[file.drive_file_id] = profile_cache_hash(content)
    workspace = FakeWorkspace([file], {file.drive_file_id: content})
    generator = QueueGenerator({})
    fallback = FakeFallback(CandidateProfile(full_name="unused"))
    service = TalentService(
        store=store,
        workspace=workspace,
        local_llm=generator,
        pdf_fallback=fallback,
        settings=_settings(),
    )

    stats = await service.sync_profiles()

    assert stats.skipped == 1
    assert stats.processed == 0
    assert generator.calls == []
    assert fallback.calls == 0


async def test_unchanged_drive_modified_time_skips_the_download() -> None:
    file = ResumeFile(
        drive_file_id="cv-fast",
        file_name="cached.docx",
        mime_type=DOCX_MIME,
        modified_time="2026-08-20T10:00:00Z",
    )
    store = FakeStore()
    store.hashes[file.drive_file_id] = profile_cache_hash(file.modified_time or "")
    workspace = FakeWorkspace([file])
    service = TalentService(
        store=store,
        workspace=workspace,
        local_llm=QueueGenerator({}),
        pdf_fallback=FakeFallback(CandidateProfile()),
        settings=_settings(),
    )

    stats = await service.sync_profiles()

    assert stats.skipped == 1
    assert workspace.download_calls == []


def test_progress_event_does_not_pollute_the_final_tool_result() -> None:
    event = TalentSearchProgressEvent(phase="syncing_resumes", label="Sincronizando currículos")

    assert event.event == "CustomEvent"
    assert event.to_dict()["phase"] == "syncing_resumes"
    assert str(event) == ""


def test_pdf_extraction_quality_detects_good_and_bad_text() -> None:
    good_pdf = _pdf_with_text(("Python FastAPI PostgreSQL experiência profissional. " * 15).strip())
    text, pages = extract_pdf_text(good_pdf)

    assert is_extraction_usable(text, pages)
    assert not is_extraction_usable("currículo curto")


async def test_low_quality_pdf_uses_gemini_fallback() -> None:
    poor_pdf = _pdf_with_text("pouco texto")
    file = ResumeFile(drive_file_id="cv-2", file_name="bruno.pdf", mime_type=PDF_MIME)
    fallback = FakeFallback(CandidateProfile(full_name="Bruno"))
    store = FakeStore()
    service = TalentService(
        store=store,
        workspace=FakeWorkspace([file], {file.drive_file_id: poor_pdf}),
        local_llm=QueueGenerator({}),
        pdf_fallback=fallback,
        settings=_settings(),
    )

    stats = await service.sync_profiles()

    assert stats.processed == 1
    assert stats.fallback_used == 1
    assert fallback.calls == 1
    candidate_id = store.profiles[file.drive_file_id].candidate_id
    assert candidate_id.startswith("candidate_")
    assert candidate_id != file.drive_file_id

    store.hashes[file.drive_file_id] = "force-reprocess"
    await service.sync_profiles()

    assert store.profiles[file.drive_file_id].candidate_id == candidate_id


def test_required_coverage_is_calculated_deterministically() -> None:
    job = JobProfile(
        title="Backend",
        criteria=[
            JobCriterion(id="python", description="Python", required=True, criterion_type="skill"),
            JobCriterion(id="sql", description="SQL", required=True, criterion_type="skill"),
            JobCriterion(id="cloud", description="Cloud", required=False, criterion_type="skill"),
        ],
    )
    candidate = CandidateProfile(candidate_id="cv-1", full_name="Ana")
    raw = CandidateAssessment(
        candidate_id="wrong",
        criteria=[CriterionAssessment(criterion_id="python", status="supported", evidence=["Python 3 anos"])],
    )

    normalized = normalize_assessment(raw, candidate, job)

    assert normalized.required_supported == 1
    assert normalized.required_total == 2
    assert normalized.criteria_coverage == 0.5
    assert [item.criterion_id for item in normalized.criteria] == ["python", "sql", "cloud"]
    assert normalized.criteria[1].status == "unclear"


async def test_ambiguous_job_does_not_access_drive() -> None:
    job = JobProfile(
        title="Indefinida",
        criteria=[],
        is_actionable=False,
        clarification_question="Qual função e quais requisitos profissionais são necessários?",
    )
    workspace = FakeWorkspace()
    service = TalentService(
        store=FakeStore(),
        workspace=workspace,
        local_llm=QueueGenerator({"JobProfile": [job]}),
        pdf_fallback=FakeFallback(CandidateProfile()),
        settings=_settings(),
    )

    result = await service.search("preciso de alguém bom")

    assert result.status == "needs_clarification"
    assert workspace.list_calls == 0
