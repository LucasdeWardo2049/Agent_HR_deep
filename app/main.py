"""FastAPI and AgentOS entry point for the Talent Search MVP."""

import asyncio
import io
import json
import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache
from os import getenv
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from agno.os import AgentOS
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

from agents.talent_search import talent_search_agent
from app.google_workspace import GOOGLE_DOC_MIME, GoogleWorkspaceClient, GoogleWorkspaceError
from app.llm import LocalLLM
from app.schemas import ResumeFile, TalentSearchRequest, TalentSearchResult
from app.settings import get_settings
from app.talent import get_talent_service
from db import TalentStore, database_is_healthy, get_postgres_db, init_talent_tables

settings = get_settings()
static_dir = Path(__file__).parent / "static"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Use Uvicorn's configured logger so audit events are emitted in containers at
# INFO level and can be collected by Docker/Portainer/Nginx logging pipelines.
audit_logger = logging.getLogger("uvicorn.error")


def get_health_llm() -> LocalLLM:
    """Reuse the OpenAI-compatible connection pool across health checks."""
    return cast(LocalLLM, get_talent_service().local_llm)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the two MVP tables before accepting traffic."""
    await asyncio.to_thread(init_talent_tables)
    yield


base_app = FastAPI(
    title="Talent Search Assistant",
    version="1.0.0",
    lifespan=lifespan,
)


@base_app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@base_app.get("/health")
async def health() -> JSONResponse:
    database_ok, model_ok = await asyncio.gather(
        asyncio.to_thread(database_is_healthy),
        get_health_llm().healthy(),
    )
    healthy = database_ok and model_ok
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "unhealthy",
            "database": "ok" if database_ok else "unavailable",
            "local_model": "ok" if model_ok else "unavailable",
        },
    )


def _coerce_agent_result(content: Any) -> TalentSearchResult:
    if isinstance(content, TalentSearchResult):
        return content
    if isinstance(content, BaseModel):
        return TalentSearchResult.model_validate(content.model_dump())
    if isinstance(content, dict):
        return TalentSearchResult.model_validate(content)
    if isinstance(content, str):
        try:
            return TalentSearchResult.model_validate(json.loads(content))
        except (json.JSONDecodeError, TypeError):
            # Some OpenAI-compatible servers ignore a forced tool choice when
            # the request is obviously ambiguous. Fail closed: treat the
            # agent's plain-language question as clarification, never as a
            # completed search and never access Drive from this branch.
            if content.strip():
                return TalentSearchResult(status="needs_clarification", message=content.strip())
    raise TypeError("The agent returned an unsupported result type")


def _coerce_agent_run(run: Any) -> TalentSearchResult:
    """Prefer the tool's structured result over any model-authored prose."""
    for execution in reversed(getattr(run, "tools", None) or []):
        if (
            getattr(execution, "tool_name", None) == "search_talent_pool"
            and not getattr(execution, "tool_call_error", False)
            and getattr(execution, "result", None)
        ):
            return _coerce_agent_result(execution.result)
    return _coerce_agent_result(run.content)


async def run_talent_agent(description: str) -> TalentSearchResult:
    """Run the public API without creating or reusing a chat session."""
    return await get_talent_service().search(description)


@base_app.post("/api/v1/talent/search", response_model=TalentSearchResult)
async def search_talent(request: TalentSearchRequest) -> TalentSearchResult:
    try:
        return await run_talent_agent(request.description)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="A busca não pôde ser concluída. Consulte os logs do servidor.",
        ) from exc


def get_cv_store() -> TalentStore:
    return TalentStore()


@cache
def get_cv_workspace() -> GoogleWorkspaceClient:
    """Reuse the user-scoped Composio session for public downloads."""
    return GoogleWorkspaceClient(settings)


def _resume_download_metadata(file_name: str, mime_type: str | None) -> tuple[str, str]:
    if mime_type == GOOGLE_DOC_MIME:
        return (f"{file_name}.txt" if not file_name.lower().endswith(".txt") else file_name, "text/plain")
    inferred = mimetypes.guess_type(file_name)[0]
    return file_name, mime_type or inferred or "application/octet-stream"


@base_app.get("/api/v1/talent/candidates/{candidate_id}/cv")
async def download_candidate_cv(
    candidate_id: str,
) -> StreamingResponse:
    user_id = "public-agent-user"
    source = await asyncio.to_thread(get_cv_store().get_profile_source, candidate_id)
    if source is None:
        audit_logger.info(
            json.dumps(
                {
                    "event": "candidate_cv_download",
                    "candidate_id": candidate_id,
                    "user_id": user_id,
                    "status": "not_found",
                }
            )
        )
        raise HTTPException(status_code=404, detail="Currículo não encontrado.")
    file_name, response_mime = _resume_download_metadata(source.file_name, source.mime_type)
    try:
        content = await get_cv_workspace().download_resume(
            ResumeFile(
                drive_file_id=source.drive_file_id,
                file_name=source.file_name,
                mime_type=source.mime_type or response_mime,
            )
        )
    except GoogleWorkspaceError as exc:
        audit_logger.warning(
            json.dumps(
                {"event": "candidate_cv_download", "candidate_id": candidate_id, "user_id": user_id, "status": "failed"}
            )
        )
        raise HTTPException(status_code=502, detail="Não foi possível obter o currículo no momento.") from exc
    audit_logger.info(
        json.dumps(
            {
                "event": "candidate_cv_download",
                "candidate_id": candidate_id,
                "user_id": user_id,
                "status": "completed",
                "bytes": len(content),
            }
        )
    )
    encoded_name = quote(Path(file_name).name, safe="")
    return StreamingResponse(
        io.BytesIO(content),
        media_type=response_mime,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@base_app.get("/api/v1/talent/searches/{search_id}/xlsx")
async def download_search_xlsx(search_id: str) -> StreamingResponse:
    search = await asyncio.to_thread(get_cv_store().get_search, search_id)
    file_id = search.get("excel_drive_file_id") if search else None
    if not file_id:
        raise HTTPException(status_code=404, detail="Planilha não encontrada.")
    try:
        content = await get_cv_workspace().download_file(str(file_id))
    except GoogleWorkspaceError as exc:
        audit_logger.warning(json.dumps({"event": "report_download", "search_id": search_id, "status": "failed"}))
        raise HTTPException(status_code=502, detail="Não foi possível obter a planilha no momento.") from exc
    audit_logger.info(
        json.dumps(
            {
                "event": "report_download",
                "search_id": search_id,
                "status": "completed",
                "bytes": len(content),
            }
        )
    )
    file_name = f"talent-search-{search_id}.xlsx"
    return StreamingResponse(
        io.BytesIO(content),
        media_type=XLSX_MIME,
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


interfaces: list[Any] = []
if getenv("SLACK_BOT_TOKEN") and getenv("SLACK_SIGNING_SECRET"):
    from agno.os.interfaces.slack import Slack

    interfaces.append(
        Slack(
            agent=talent_search_agent,
            streaming=True,
            token=getenv("SLACK_BOT_TOKEN", ""),
            signing_secret=getenv("SLACK_SIGNING_SECRET", ""),
            resolve_user_identity=True,
            loading_text="Buscando talentos...",
        )
    )

mcp_auth = None
if getenv("MCP_CONNECT_SECRET"):
    from agno.os import AgentOSBuiltinAuth

    mcp_auth = AgentOSBuiltinAuth(
        url=getenv("AGENTOS_URL", "http://127.0.0.1:8000"),
        secret=getenv("MCP_CONNECT_SECRET", ""),
        signing_key_material=getenv("AGENTOS_MCP_SIGNING_KEY"),
    )

agent_os = AgentOS(
    name="Talent Search Assistant",
    tracing=True,
    scheduler=False,
    authorization=False,
    mcp_server=True,
    mcp_auth=mcp_auth,
    db=get_postgres_db(),
    agents=[talent_search_agent],
    interfaces=interfaces,
    config=str(Path(__file__).parent / "config.yaml"),
    base_app=base_app,
    on_route_conflict="preserve_base_app",
)
app = agent_os.get_app()


def _allow_local_agentos_private_network(agentos_app: FastAPI) -> None:
    """Allow os.agno.com to reach this local server from the browser."""
    for middleware in agentos_app.user_middleware:
        if middleware.cls is CORSMiddleware:
            middleware.kwargs["allow_private_network"] = True
            agentos_app.middleware_stack = None
            return
    raise RuntimeError("AgentOS CORS middleware is unavailable")


_allow_local_agentos_private_network(app)


if __name__ == "__main__":
    agent_os.serve(app="app.main:app", reload=False)
