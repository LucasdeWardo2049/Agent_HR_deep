"""Typed contracts used by the agent, service, API, database, and reports."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ProfessionalField = Literal["education", "language", "experience", "skill", "certification"]


class ProfileEvidence(StrictModel):
    field: ProfessionalField
    fact: str = Field(min_length=1, max_length=500)
    source_excerpt: str = Field(min_length=1, max_length=500)


class CandidateProfile(StrictModel):
    candidate_id: str = ""
    full_name: str | None = None
    education: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    experiences: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    evidence: list[ProfileEvidence] = Field(default_factory=list)
    relevant_experience_years: float | None = Field(default=None, ge=0)
    source_drive_file_id: str = ""
    source_drive_url: str | None = None


CriterionType = Literal["education", "language", "experience", "skill", "certification", "other"]


class JobCriterion(StrictModel):
    id: str
    description: str
    required: bool
    criterion_type: CriterionType


class JobProfile(StrictModel):
    title: str
    summary: str | None = None
    criteria: list[JobCriterion] = Field(default_factory=list)
    is_actionable: bool = True
    clarification_question: str | None = None

    @model_validator(mode="after")
    def validate_actionability(self) -> "JobProfile":
        if self.is_actionable and not self.criteria:
            raise ValueError("An actionable job profile must contain at least one criterion")
        if not self.is_actionable and not self.clarification_question:
            raise ValueError("A non-actionable job profile must include a clarification question")
        return self


CriterionStatus = Literal["supported", "partial", "not_found", "unclear"]


class CriterionAssessment(StrictModel):
    criterion_id: str
    status: CriterionStatus
    evidence: list[str] = Field(default_factory=list)
    notes: str | None = None


class CandidateAssessment(StrictModel):
    candidate_id: str
    candidate_name: str | None = None
    criteria: list[CriterionAssessment] = Field(default_factory=list)
    points_to_confirm: list[str] = Field(default_factory=list)
    professional_summary: str | None = None
    required_supported: int = Field(default=0, ge=0)
    required_total: int = Field(default=0, ge=0)
    criteria_coverage: float = Field(default=0, ge=0, le=1)


SearchStatus = Literal["completed", "needs_clarification", "failed"]


class TalentSearchResult(StrictModel):
    status: SearchStatus
    message: str
    search_id: str | None = None
    candidates_analyzed: int = Field(default=0, ge=0)
    google_sheet_url: str | None = None
    excel_url: str | None = None
    excel_drive_file_id: str | None = Field(default=None, exclude=True)
    warnings: list[str] = Field(default_factory=list)


class TalentSearchRequest(StrictModel):
    description: str = Field(min_length=3, max_length=5000)


class ResumeFile(StrictModel):
    drive_file_id: str
    file_name: str
    mime_type: str
    drive_url: str | None = None
    modified_time: str | None = None
