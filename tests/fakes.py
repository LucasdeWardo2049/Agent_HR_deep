from typing import Any

from pydantic import BaseModel

from app.google_workspace import ReportArtifacts
from app.schemas import CandidateAssessment, CandidateProfile, JobProfile, ResumeFile, TalentSearchResult


class FakeStore:
    def __init__(self) -> None:
        self.hashes: dict[str, str] = {}
        self.profiles: dict[str, CandidateProfile] = {}
        self.saved_searches: list[TalentSearchResult] = []

    def get_source_hash(self, drive_file_id: str) -> str | None:
        return self.hashes.get(drive_file_id)

    def get_candidate_id(self, drive_file_id: str) -> str | None:
        profile = self.profiles.get(drive_file_id)
        return profile.candidate_id if profile else None

    def upsert_profile(
        self,
        *,
        file_name: str,
        mime_type: str,
        source_hash: str,
        profile: CandidateProfile,
        parser_provider: str,
        model_name: str,
        fallback_used: bool,
    ) -> None:
        del file_name, mime_type, parser_provider, model_name, fallback_used
        self.hashes[profile.source_drive_file_id] = source_hash
        self.profiles[profile.source_drive_file_id] = profile

    def update_source_metadata(
        self,
        *,
        drive_file_id: str,
        file_name: str,
        mime_type: str,
        drive_url: str | None,
    ) -> None:
        del drive_file_id, file_name, mime_type, drive_url

    def list_profiles(self, drive_file_ids: set[str] | None = None) -> list[CandidateProfile]:
        profiles = list(self.profiles.values())
        if drive_file_ids is None:
            return profiles
        return [profile for profile in profiles if profile.source_drive_file_id in drive_file_ids]

    def save_search(
        self,
        *,
        description: str,
        job_profile: JobProfile | None,
        assessments: list[CandidateAssessment],
        result: TalentSearchResult,
    ) -> None:
        del description, job_profile, assessments
        self.saved_searches.append(result)


class FakeWorkspace:
    def __init__(self, files: list[ResumeFile] | None = None, content: dict[str, bytes] | None = None) -> None:
        self.files = files or []
        self.content = content or {}
        self.list_calls = 0
        self.download_calls: list[str] = []
        self.report_calls = 0

    async def list_resume_files(self) -> list[ResumeFile]:
        self.list_calls += 1
        return self.files

    async def download_resume(self, file: ResumeFile) -> bytes:
        self.download_calls.append(file.drive_file_id)
        return self.content[file.drive_file_id]

    async def create_report(
        self,
        job_profile: JobProfile,
        assessments: list[CandidateAssessment],
        profiles: list[CandidateProfile],
    ) -> ReportArtifacts:
        del job_profile, assessments, profiles
        self.report_calls += 1
        return ReportArtifacts(
            google_sheet_url="https://docs.google.com/spreadsheets/d/test-sheet",
            excel_url="https://drive.google.com/open?id=test-xlsx",
            excel_file_id="test-xlsx",
        )


class QueueGenerator:
    def __init__(self, responses: dict[str, list[BaseModel]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, str]] = []

    async def generate(self, schema: type[BaseModel], system_prompt: str, user_input: str) -> Any:
        self.calls.append((schema.__name__, system_prompt, user_input))
        queue = self.responses[schema.__name__]
        if not queue:
            raise AssertionError(f"No fake response left for {schema.__name__}")
        return queue.pop(0)


class FakeFallback:
    def __init__(self, profile: CandidateProfile) -> None:
        self.profile = profile
        self.calls = 0

    async def parse(self, pdf_bytes: bytes) -> CandidateProfile:
        assert pdf_bytes
        self.calls += 1
        return self.profile
