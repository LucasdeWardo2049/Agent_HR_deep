"""Central configuration and model factories for the Talent Search MVP."""

from functools import cache
from urllib.parse import quote

from agno.models.openai.like import OpenAILike
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration.

    External credentials are validated when their integration is used, allowing
    the API and fast offline tests to start without Google or Gemini access.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("ENVIRONMENT", "RUNTIME_ENV"),
    )
    local_llm_base_url: str = Field(
        default="http://192.168.4.114:4000/v1",
        validation_alias=AliasChoices("LOCAL_LLM_BASE_URL", "OPENAI_BASE_URL"),
    )
    local_llm_api_key: str = Field(
        default="mock-key",
        validation_alias=AliasChoices("LOCAL_LLM_API_KEY", "OPENAI_API_KEY"),
    )
    local_llm_model: str = Field(
        default="gpt-oss-120b",
        validation_alias=AliasChoices("LOCAL_LLM_MODEL", "OPENAI_MODEL_ID"),
    )
    agent_chat_model: str = "qwen-fast"

    gemini_api_key: str | None = None
    gemini_pdf_model: str | None = None

    composio_api_key: str | None = None
    composio_user_id: str | None = None
    composio_search_version: str = "20260618_00"
    composio_googledrive_version: str = "20260815_00"
    composio_googlesheets_version: str = "20260813_00"
    composio_request_timeout_seconds: int = Field(default=30, ge=5, le=120)
    composio_max_retries: int = Field(default=0, ge=0, le=3)
    job_research_cache_ttl_seconds: int = Field(default=900, ge=0, le=86_400)
    serp_api_key: str | None = None
    google_drive_talent_pool_folder_id: str | None = None
    results_drive_folder_id: str | None = None
    public_app_url: str = Field(
        default="http://localhost:8000",
        validation_alias=AliasChoices("PUBLIC_APP_URL", "AGENTOS_URL"),
    )
    assessment_concurrency: int = Field(default=4, ge=1, le=16)

    db_driver: str = "postgresql+psycopg"
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "ai"
    db_pass: str = "ai"
    db_database: str = "ai"

    @model_validator(mode="after")
    def validate_gemini_pair(self) -> "Settings":
        if bool(self.gemini_api_key) != bool(self.gemini_pdf_model):
            raise ValueError("GEMINI_API_KEY and GEMINI_PDF_MODEL must be configured together")
        return self

    @property
    def database_url(self) -> str:
        password = quote(self.db_pass, safe="")
        return f"{self.db_driver}://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_database}"

    def require_google(self) -> None:
        missing = [
            name
            for name, value in (
                ("COMPOSIO_API_KEY", self.composio_api_key),
                ("COMPOSIO_USER_ID", self.composio_user_id),
                ("GOOGLE_DRIVE_TALENT_POOL_FOLDER_ID", self.google_drive_talent_pool_folder_id),
                ("RESULTS_DRIVE_FOLDER_ID", self.results_drive_folder_id),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing Google integration settings: {', '.join(missing)}")


@cache
def get_settings() -> Settings:
    return Settings()


def default_model() -> OpenAILike:
    """Return a fresh Agno model connected to the local OpenAI-compatible API."""
    settings = get_settings()
    return OpenAILike(
        id=settings.local_llm_model,
        base_url=settings.local_llm_base_url,
        api_key=settings.local_llm_api_key,
    )


def chat_model() -> OpenAILike:
    """Return the low-latency model used for chat and tool routing."""
    settings = get_settings()
    return OpenAILike(
        id=settings.agent_chat_model,
        base_url=settings.local_llm_base_url,
        api_key=settings.local_llm_api_key,
        temperature=0,
        max_tokens=900,
        timeout=30,
        retries=0,
        max_retries=0,
    )
