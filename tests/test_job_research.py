import asyncio
import json

import pytest

from app.job_research import JobProfileResearchService, ResearchSource, _extract_serpapi_sources
from app.settings import Settings


def source(index: int, *, provider: str = "composio_search", url: str | None = None) -> ResearchSource:
    return ResearchSource(
        title=f"Fonte {index}",
        url=url or f"https://example{index}.com/role",
        snippet=f"Evidência pública {index}",
        provider=provider,
    )


def settings(*, serp: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        local_llm_api_key="test-key",
        composio_api_key="composio-test",
        serp_api_key="serp-test" if serp else None,
    )


@pytest.mark.asyncio
async def test_composio_with_three_sources_does_not_call_serpapi() -> None:
    fallback_calls = 0

    async def primary(_: str) -> list[ResearchSource]:
        return [source(1), source(2), source(3)]

    async def fallback(_: str) -> list[ResearchSource]:
        nonlocal fallback_calls
        fallback_calls += 1
        return [source(4, provider="serpapi")]

    result = await JobProfileResearchService(
        settings(),
        composio_search=primary,
        serpapi_search=fallback,
    ).research("Engenheiro de software")

    assert result.status == "completed"
    assert result.providers_used == ["composio_search"]
    assert len(result.sources) == 3
    assert fallback_calls == 0


@pytest.mark.asyncio
async def test_few_composio_sources_trigger_serpapi_and_deduplicate_urls() -> None:
    duplicate_url = "https://www.example1.com/role/"

    async def primary(_: str) -> list[ResearchSource]:
        return [source(1), source(2)]

    async def fallback(_: str) -> list[ResearchSource]:
        return [
            source(10, provider="serpapi", url=duplicate_url),
            source(3, provider="serpapi"),
            source(4, provider="serpapi"),
            source(5, provider="serpapi"),
            source(6, provider="serpapi"),
        ]

    result = await JobProfileResearchService(
        settings(),
        composio_search=primary,
        serpapi_search=fallback,
    ).research("Analista de dados")

    assert result.status == "completed"
    assert result.providers_used == ["composio_search", "serpapi"]
    assert len(result.sources) == 5
    assert len({item.url.replace("www.", "").rstrip("/") for item in result.sources}) == 5


@pytest.mark.asyncio
async def test_composio_timeout_uses_serpapi() -> None:
    async def primary(_: str) -> list[ResearchSource]:
        await asyncio.sleep(0.05)
        return []

    async def fallback(_: str) -> list[ResearchSource]:
        return [source(1, provider="serpapi"), source(2, provider="serpapi"), source(3, provider="serpapi")]

    result = await JobProfileResearchService(
        settings(),
        composio_search=primary,
        serpapi_search=fallback,
        timeout_seconds=0.001,
    ).research("Product manager")

    assert result.status == "partial"
    assert result.providers_used == ["serpapi"]
    assert "tempo limite" in result.warnings[0]


@pytest.mark.asyncio
async def test_both_providers_failing_returns_unavailable_without_leaking_errors() -> None:
    async def fail(_: str) -> list[ResearchSource]:
        raise RuntimeError("secret provider body")

    result = await JobProfileResearchService(
        settings(),
        composio_search=fail,
        serpapi_search=fail,
    ).research("DevOps")

    assert result.status == "unavailable"
    assert result.sources == []
    assert len(result.warnings) == 2
    assert "secret provider body" not in " ".join(result.warnings)


def test_serpapi_results_are_bounded_to_safe_fields() -> None:
    payload = json.dumps(
        {
            "search_results": [
                {
                    "title": "T" * 300,
                    "link": "https://example.com/job",
                    "snippet": "S" * 900,
                    "raw_html": "must not reach the model",
                }
            ]
        }
    )

    result = _extract_serpapi_sources(payload)

    assert len(result[0].title) == 180
    assert len(result[0].snippet) == 500
    assert "raw_html" not in result[0].model_dump_json()


@pytest.mark.asyncio
async def test_job_research_never_constructs_google_workspace() -> None:
    calls: list[str] = []

    async def primary(query: str) -> list[ResearchSource]:
        calls.append(query)
        return [source(1), source(2), source(3)]

    async def unused_fallback(_: str) -> list[ResearchSource]:
        raise AssertionError("fallback should not run")

    result = await JobProfileResearchService(
        settings(),
        composio_search=primary,
        serpapi_search=unused_fallback,
    ).research("UX designer")

    assert result.status == "completed"
    assert calls == ["UX designer"]


@pytest.mark.asyncio
async def test_equal_research_requests_are_coalesced_and_cached() -> None:
    call_count = 0

    async def primary(_: str) -> list[ResearchSource]:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return [source(1), source(2), source(3)]

    async def unused_fallback(_: str) -> list[ResearchSource]:
        raise AssertionError("fallback should not run")

    service = JobProfileResearchService(
        settings(),
        composio_search=primary,
        serpapi_search=unused_fallback,
    )
    first, second = await asyncio.gather(
        service.research("Engenheiro de dados"),
        service.research("engenheiro de dados"),
    )
    third = await service.research("Engenheiro de dados")

    assert first.status == second.status == third.status == "completed"
    assert call_count == 1
