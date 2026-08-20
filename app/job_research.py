"""Public job-profile research with a bounded Composio/SerpApi fallback."""

import asyncio
import json
import re
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from functools import cache
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

from agno.tools import tool
from pydantic import BaseModel, Field

from app.settings import Settings, get_settings

COMPOSIO_SEARCH_TOOL = "COMPOSIO_SEARCH_WEB"
MIN_PRIMARY_SOURCES = 3
MAX_SOURCES = 5
MAX_TITLE_LENGTH = 180
MAX_SNIPPET_LENGTH = 500
SEARCH_TIMEOUT_SECONDS = 8.0


class ResearchSource(BaseModel):
    """One bounded public source returned to the chat model."""

    title: str
    url: str
    snippet: str = ""
    provider: Literal["composio_search", "serpapi"]


class JobResearchResult(BaseModel):
    """Provider-independent job-profile research result."""

    status: Literal["completed", "partial", "unavailable"]
    query: str
    summary: str = ""
    sources: list[ResearchSource] = Field(default_factory=list)
    citation_markdown: list[str] = Field(default_factory=list)
    providers_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


SearchResponse = list[ResearchSource] | tuple[list[ResearchSource], str]
SearchCallable = Callable[[str], Awaitable[SearchResponse]]


class JobProfileResearchService:
    """Research roles only; it never searches for or evaluates people."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        composio_search: SearchCallable | None = None,
        serpapi_search: SearchCallable | None = None,
        timeout_seconds: float = SEARCH_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings or get_settings()
        self._composio_search = composio_search or self._search_composio
        self._serpapi_search = serpapi_search or self._search_serpapi
        self.timeout_seconds = timeout_seconds
        self._composio_client: Any | None = None
        self._composio_session: Any | None = None
        self._serpapi_toolkit: Any | None = None
        self._client_lock = threading.Lock()
        self._cache_lock = asyncio.Lock()
        self._cache: dict[str, tuple[float, JobResearchResult]] = {}
        self._inflight: dict[str, asyncio.Task[JobResearchResult]] = {}

    async def research(self, query: str) -> JobResearchResult:
        """Search public role data, coalescing equal requests and caching complete results."""
        clean_query = _bounded_text(query, 500)
        if not clean_query:
            return JobResearchResult(
                status="unavailable",
                query="",
                warnings=["Informe o cargo ou o contexto da vaga que deseja pesquisar."],
            )
        cache_key = clean_query.casefold()
        now = time.monotonic()
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1].model_copy(deep=True)
            if cached:
                self._cache.pop(cache_key, None)
            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(self._research_uncached(clean_query))
                self._inflight[cache_key] = task

        try:
            result = await asyncio.shield(task)
            if result.status == "completed" and self.settings.job_research_cache_ttl_seconds:
                expires_at = time.monotonic() + self.settings.job_research_cache_ttl_seconds
                async with self._cache_lock:
                    self._cache[cache_key] = (expires_at, result.model_copy(deep=True))
            return result
        finally:
            if task.done():
                async with self._cache_lock:
                    if self._inflight.get(cache_key) is task:
                        self._inflight.pop(cache_key, None)

    async def _research_uncached(self, clean_query: str) -> JobResearchResult:
        """Search Composio first and supplement with SerpApi when needed."""

        sources: list[ResearchSource] = []
        summary = ""
        providers_used: list[str] = []
        warnings: list[str] = []

        if self.settings.composio_api_key:
            try:
                primary_response = await asyncio.wait_for(
                    self._composio_search(clean_query),
                    timeout=self.timeout_seconds,
                )
                if isinstance(primary_response, tuple):
                    primary, summary = primary_response
                else:
                    primary = primary_response
                providers_used.append("composio_search")
                sources.extend(primary)
            except TimeoutError:
                warnings.append("A pesquisa pelo Composio excedeu o tempo limite.")
            except Exception as exc:
                warnings.append(f"A pesquisa pelo Composio falhou ({type(exc).__name__}).")
        else:
            warnings.append("COMPOSIO_API_KEY não está configurada para pesquisa pública.")

        unique_primary = _deduplicate_sources(sources)
        if len(unique_primary) < MIN_PRIMARY_SOURCES:
            if self.settings.serp_api_key:
                try:
                    fallback_response = await asyncio.wait_for(
                        self._serpapi_search(clean_query),
                        timeout=self.timeout_seconds,
                    )
                    fallback = (
                        fallback_response[0]
                        if isinstance(fallback_response, tuple)
                        else fallback_response
                    )
                    providers_used.append("serpapi")
                    sources.extend(fallback)
                except TimeoutError:
                    warnings.append("A pesquisa pelo SerpApi excedeu o tempo limite.")
                except Exception as exc:
                    warnings.append(f"A pesquisa pelo SerpApi falhou ({type(exc).__name__}).")
            else:
                warnings.append("SERP_API_KEY não está configurada para complementar a pesquisa.")

        sources = _deduplicate_sources(sources)[:MAX_SOURCES]
        if not sources:
            status: Literal["completed", "partial", "unavailable"] = "unavailable"
        elif len(sources) < MIN_PRIMARY_SOURCES or warnings:
            status = "partial"
        else:
            status = "completed"
        return JobResearchResult(
            status=status,
            query=clean_query,
            summary=summary,
            sources=sources,
            citation_markdown=[f"- [{source.title}]({source.url})" for source in sources],
            providers_used=providers_used,
            warnings=warnings,
        )

    def _get_composio_session(self) -> Any:
        if self._composio_session is not None:
            return self._composio_session
        with self._client_lock:
            if self._composio_session is not None:
                return self._composio_session
            from composio import Composio

            self._composio_client = Composio(
                api_key=cast(str, self.settings.composio_api_key),
                toolkit_versions={"composio_search": self.settings.composio_search_version},
                timeout=max(1, int(self.timeout_seconds)),
                max_retries=self.settings.composio_max_retries,
            )
            self._composio_session = self._composio_client.create(
                user_id=self.settings.composio_user_id or "talent-search-research",
                toolkits=["composio_search"],
                manage_connections=False,
                sandbox={"enable": False},
            )
        return self._composio_session

    def _execute_composio(self, query: str) -> Any:
        return self._get_composio_session().execute(
            COMPOSIO_SEARCH_TOOL,
            arguments={"query": _role_search_query(query)},
        )

    async def _search_composio(self, query: str) -> SearchResponse:
        # Session creation is synchronous too, so the complete path belongs in
        # the worker thread; otherwise the first request blocks every SSE run.
        response = await asyncio.to_thread(self._execute_composio, query)
        return _extract_composio_sources(response), _extract_composio_answer(response)

    def _get_serpapi_toolkit(self) -> Any:
        if self._serpapi_toolkit is not None:
            return self._serpapi_toolkit
        with self._client_lock:
            if self._serpapi_toolkit is None:
                from agno.tools.serpapi import SerpApiTools

                self._serpapi_toolkit = SerpApiTools(api_key=cast(str, self.settings.serp_api_key))
        return self._serpapi_toolkit

    def _execute_serpapi(self, query: str) -> str:
        return cast(str, self._get_serpapi_toolkit().search_google(_role_search_query(query), MAX_SOURCES))

    async def _search_serpapi(self, query: str) -> list[ResearchSource]:
        response = await asyncio.to_thread(self._execute_serpapi, query)
        return _extract_serpapi_sources(response)


def _role_search_query(query: str) -> str:
    return (
        f"{query} responsabilidades competências requisitos evidências para entrevista perfil profissional; "
        "responda em português do Brasil de forma concisa"
    )


def _bounded_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, Mapping) else None
    return None


def _walk_mappings(value: Any) -> list[Mapping[str, Any]]:
    pending: list[Any] = [value]
    mappings: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        mapping = _as_mapping(current)
        if mapping is not None:
            marker = id(mapping)
            if marker in seen:
                continue
            seen.add(marker)
            mappings.append(mapping)
            pending.extend(mapping.values())
        elif isinstance(current, list):
            pending.extend(current)
    return mappings


def _extract_composio_sources(response: Any) -> list[ResearchSource]:
    citations: list[Any] = []
    for mapping in _walk_mappings(response):
        candidate = mapping.get("citations")
        if isinstance(candidate, list):
            citations.extend(candidate)

    sources: list[ResearchSource] = []
    for citation in citations:
        raw_url: Any
        raw_title: Any
        raw_snippet: Any
        if isinstance(citation, str):
            raw_url = citation
            raw_title = urlsplit(citation).netloc or "Fonte pública"
            raw_snippet = ""
        else:
            citation_mapping = _as_mapping(citation)
            if citation_mapping is None:
                continue
            raw_url = citation_mapping.get("url") or citation_mapping.get("link") or citation_mapping.get("href")
            raw_title = (
                citation_mapping.get("title")
                or citation_mapping.get("name")
                or citation_mapping.get("source")
                or "Fonte pública"
            )
            raw_snippet = (
                citation_mapping.get("snippet")
                or citation_mapping.get("text")
                or citation_mapping.get("description")
                or ""
            )
        if _is_public_http_url(raw_url):
            sources.append(
                ResearchSource(
                    title=_bounded_text(raw_title, MAX_TITLE_LENGTH),
                    url=str(raw_url),
                    snippet=_bounded_text(raw_snippet, MAX_SNIPPET_LENGTH),
                    provider="composio_search",
                )
            )
    return _deduplicate_sources(sources)


def _extract_composio_answer(response: Any) -> str:
    """Keep only Composio Search's bounded public synthesis, never its raw envelope."""
    for mapping in _walk_mappings(response):
        answer = mapping.get("answer")
        if isinstance(answer, str) and answer.strip():
            return _bounded_text(answer, 1_600)
    return ""


def _extract_serpapi_sources(response: str) -> list[ResearchSource]:
    try:
        payload = json.loads(response)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("SerpApi returned an invalid response") from exc
    records = payload.get("search_results")
    if not isinstance(records, list):
        raise RuntimeError("SerpApi returned no organic results")
    sources: list[ResearchSource] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        url = record.get("link") or record.get("url")
        if not _is_public_http_url(url):
            continue
        sources.append(
            ResearchSource(
                title=_bounded_text(record.get("title") or "Fonte pública", MAX_TITLE_LENGTH),
                url=str(url),
                snippet=_bounded_text(record.get("snippet"), MAX_SNIPPET_LENGTH),
                provider="serpapi",
            )
        )
    return _deduplicate_sources(sources)


def _is_public_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    host = parsed.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, parsed.query, ""))


def _deduplicate_sources(sources: list[ResearchSource]) -> list[ResearchSource]:
    unique: list[ResearchSource] = []
    seen: set[str] = set()
    for source in sources:
        key = _canonical_url(source.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(source)
    return unique


@cache
def get_job_research_service() -> JobProfileResearchService:
    return JobProfileResearchService()


@tool
async def research_job_profile(role_description: str) -> str:
    """Pesquise informações públicas sobre um cargo, nunca pessoas ou candidatos."""
    result = await get_job_research_service().research(role_description)
    return result.model_dump_json()
