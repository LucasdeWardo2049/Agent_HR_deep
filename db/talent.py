"""Minimal PostgreSQL persistence for cached profiles and completed searches."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, create_engine, text

from app.schemas import CandidateAssessment, CandidateProfile, JobProfile, TalentSearchResult
from db.url import db_url


@dataclass(frozen=True)
class TalentProfileSource:
    drive_file_id: str
    file_name: str
    mime_type: str | None
    drive_url: str | None


@cache
def get_engine() -> Engine:
    return create_engine(db_url, pool_pre_ping=True)


def init_talent_tables() -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS talent_profiles (
            drive_file_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            drive_url TEXT,
            mime_type TEXT,
            source_hash TEXT NOT NULL,
            profile JSONB NOT NULL,
            parser_provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
            processed_at TIMESTAMPTZ NOT NULL
        )
        """,
        "ALTER TABLE talent_profiles ADD COLUMN IF NOT EXISTS mime_type TEXT",
        "ALTER TABLE talent_profiles ADD COLUMN IF NOT EXISTS candidate_id TEXT",
        """
        CREATE TABLE IF NOT EXISTS talent_searches (
            search_id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            job_profile JSONB,
            assessments JSONB NOT NULL DEFAULT '[]'::jsonb,
            candidates_analyzed INTEGER NOT NULL DEFAULT 0,
            google_sheet_url TEXT,
            excel_url TEXT,
            excel_drive_file_id TEXT,
            status TEXT NOT NULL,
            warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        "ALTER TABLE talent_searches ADD COLUMN IF NOT EXISTS excel_drive_file_id TEXT",
        # Cache of structured model answers. `cache_key` already encodes a
        # pipeline version, so a prompt or schema change misses instead of
        # serving stale content. Sample answers are never written here.
        """
        CREATE TABLE IF NOT EXISTS talent_llm_cache (
            cache_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_talent_llm_cache_created_at ON talent_llm_cache (created_at)",
    )
    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        legacy_rows = connection.execute(
            text(
                """
                SELECT drive_file_id, candidate_id
                FROM talent_profiles
                WHERE candidate_id IS NULL OR candidate_id = drive_file_id
                """
            )
        ).mappings()
        for row in legacy_rows:
            candidate_id = f"candidate_{uuid4().hex}"
            connection.execute(
                text(
                    """
                    UPDATE talent_profiles
                    SET candidate_id = :candidate_id,
                        profile = jsonb_set(
                            profile,
                            '{candidate_id}',
                            to_jsonb(CAST(:candidate_id AS text)),
                            true
                        )
                    WHERE drive_file_id = :drive_file_id
                    """
                ),
                {"candidate_id": candidate_id, "drive_file_id": row["drive_file_id"]},
            )
        connection.execute(text("ALTER TABLE talent_profiles ALTER COLUMN candidate_id SET NOT NULL"))
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_talent_profiles_candidate_id ON talent_profiles (candidate_id)")
        )


def database_is_healthy() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


class TalentStore:
    """Small synchronous store; async callers offload it to a worker thread."""

    def get_source_hash(self, drive_file_id: str) -> str | None:
        with get_engine().connect() as connection:
            row = (
                connection.execute(
                    text("SELECT source_hash FROM talent_profiles WHERE drive_file_id = :drive_file_id"),
                    {"drive_file_id": drive_file_id},
                )
                .mappings()
                .first()
            )
        return str(row["source_hash"]) if row else None

    def get_candidate_id(self, drive_file_id: str) -> str | None:
        with get_engine().connect() as connection:
            row = (
                connection.execute(
                    text("SELECT candidate_id FROM talent_profiles WHERE drive_file_id = :drive_file_id"),
                    {"drive_file_id": drive_file_id},
                )
                .mappings()
                .first()
            )
        return str(row["candidate_id"]) if row else None

    def get_existing_metadata(self, drive_file_ids: list[str]) -> dict[str, dict[str, str | None]]:
        if not drive_file_ids:
            return {}

        placeholders = ", ".join(f":id_{i}" for i in range(len(drive_file_ids)))
        params = {f"id_{i}": file_id for i, file_id in enumerate(drive_file_ids)}

        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT drive_file_id, source_hash, candidate_id "
                    f"FROM talent_profiles WHERE drive_file_id IN ({placeholders})"
                ),
                params,
            ).mappings().all()

        return {
            str(row["drive_file_id"]): {
                "source_hash": str(row["source_hash"]) if row["source_hash"] else None,
                "candidate_id": str(row["candidate_id"]) if row["candidate_id"] else None,
            }
            for row in rows
        }

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
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO talent_profiles (
                        drive_file_id, candidate_id, file_name, drive_url, mime_type, source_hash, profile,
                        parser_provider, model_name, fallback_used, processed_at
                    ) VALUES (
                        :drive_file_id, :candidate_id, :file_name, :drive_url, :mime_type, :source_hash,
                        CAST(:profile AS jsonb), :parser_provider, :model_name,
                        :fallback_used, :processed_at
                    )
                    ON CONFLICT (drive_file_id) DO UPDATE SET
                        candidate_id = EXCLUDED.candidate_id,
                        file_name = EXCLUDED.file_name,
                        drive_url = EXCLUDED.drive_url,
                        mime_type = EXCLUDED.mime_type,
                        source_hash = EXCLUDED.source_hash,
                        profile = EXCLUDED.profile,
                        parser_provider = EXCLUDED.parser_provider,
                        model_name = EXCLUDED.model_name,
                        fallback_used = EXCLUDED.fallback_used,
                        processed_at = EXCLUDED.processed_at
                    """
                ),
                {
                    "drive_file_id": profile.source_drive_file_id,
                    "candidate_id": profile.candidate_id,
                    "file_name": file_name,
                    "drive_url": profile.source_drive_url,
                    "mime_type": mime_type,
                    "source_hash": source_hash,
                    "profile": profile.model_dump_json(),
                    "parser_provider": parser_provider,
                    "model_name": model_name,
                    "fallback_used": fallback_used,
                    "processed_at": datetime.now(UTC),
                },
            )

    def update_source_metadata(
        self,
        *,
        drive_file_id: str,
        file_name: str,
        mime_type: str,
        drive_url: str | None,
    ) -> None:
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE talent_profiles
                    SET file_name = :file_name,
                        mime_type = :mime_type,
                        drive_url = :drive_url
                    WHERE drive_file_id = :drive_file_id
                    """
                ),
                {
                    "drive_file_id": drive_file_id,
                    "file_name": file_name,
                    "mime_type": mime_type,
                    "drive_url": drive_url,
                },
            )

    def get_profile_source(self, candidate_id: str) -> TalentProfileSource | None:
        with get_engine().connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT drive_file_id, file_name, mime_type, drive_url
                        FROM talent_profiles
                        WHERE candidate_id = :candidate_id
                        """
                    ),
                    {"candidate_id": candidate_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return TalentProfileSource(
            drive_file_id=str(row["drive_file_id"]),
            file_name=str(row["file_name"]),
            mime_type=str(row["mime_type"]) if row["mime_type"] else None,
            drive_url=str(row["drive_url"]) if row["drive_url"] else None,
        )

    def list_profiles(self, drive_file_ids: set[str] | None = None) -> list[CandidateProfile]:
        with get_engine().connect() as connection:
            rows = connection.execute(text("SELECT candidate_id, profile FROM talent_profiles")).mappings().all()
        profiles = [
            CandidateProfile.model_validate(row["profile"]).model_copy(
                update={"candidate_id": str(row["candidate_id"])}
            )
            for row in rows
        ]
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
        if result.search_id is None:
            return
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO talent_searches (
                        search_id, description, job_profile, assessments,
                        candidates_analyzed, google_sheet_url, excel_url, excel_drive_file_id,
                        status, warnings, created_at
                    ) VALUES (
                        :search_id, :description, CAST(:job_profile AS jsonb),
                        CAST(:assessments AS jsonb), :candidates_analyzed,
                        :google_sheet_url, :excel_url, :excel_drive_file_id, :status,
                        CAST(:warnings AS jsonb), :created_at
                    )
                    ON CONFLICT (search_id) DO UPDATE SET
                        job_profile = EXCLUDED.job_profile,
                        assessments = EXCLUDED.assessments,
                        candidates_analyzed = EXCLUDED.candidates_analyzed,
                        google_sheet_url = EXCLUDED.google_sheet_url,
                        excel_url = EXCLUDED.excel_url,
                        excel_drive_file_id = EXCLUDED.excel_drive_file_id,
                        status = EXCLUDED.status,
                        warnings = EXCLUDED.warnings
                    """
                ),
                {
                    "search_id": result.search_id,
                    "description": description,
                    "job_profile": job_profile.model_dump_json() if job_profile else "null",
                    "assessments": json.dumps([item.model_dump(mode="json") for item in assessments]),
                    "candidates_analyzed": result.candidates_analyzed,
                    "google_sheet_url": result.google_sheet_url,
                    "excel_url": result.excel_url,
                    "excel_drive_file_id": result.excel_drive_file_id,
                    "status": result.status,
                    "warnings": json.dumps(result.warnings),
                    "created_at": datetime.now(UTC),
                },
            )

    def get_cached_json(self, cache_key: str) -> dict[str, Any] | None:
        """Return a cached model answer, or None when absent."""
        with get_engine().connect() as connection:
            row = (
                connection.execute(
                    text("SELECT payload FROM talent_llm_cache WHERE cache_key = :cache_key"),
                    {"cache_key": cache_key},
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        payload = row["payload"]
        return dict(payload) if isinstance(payload, dict) else None

    def put_cached_json(self, cache_key: str, kind: str, payload: dict[str, Any]) -> None:
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO talent_llm_cache (cache_key, kind, payload, created_at)
                    VALUES (:cache_key, :kind, CAST(:payload AS jsonb), :created_at)
                    ON CONFLICT (cache_key) DO UPDATE SET
                        kind = EXCLUDED.kind,
                        payload = EXCLUDED.payload,
                        created_at = EXCLUDED.created_at
                    """
                ),
                {
                    "cache_key": cache_key,
                    "kind": kind,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "created_at": datetime.now(UTC),
                },
            )

    def get_search(self, search_id: str) -> dict[str, Any] | None:
        with get_engine().connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM talent_searches WHERE search_id = :search_id"),
                    {"search_id": search_id},
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None
