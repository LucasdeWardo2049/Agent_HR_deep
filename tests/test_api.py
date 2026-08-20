from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

import app.main as main
from app.schemas import TalentSearchResult
from db import TalentProfileSource


def test_interface_is_served_without_a_frontend_build() -> None:
    response = TestClient(main.app).get("/")

    assert response.status_code == 200
    assert "Quem você procura?" in response.text
    assert 'fetch("/api/v1/talent/search"' in response.text
    assert 'id="messages"' in response.text
    assert 'id="composer-form"' in response.text
    assert "createFileCard" in response.text
    assert "Relatório completo.xlsx" in response.text
    assert "Sem ranking ou decisão automática" in response.text
    assert "progress-panel" not in response.text


def test_agentos_allows_browser_private_network_preflight() -> None:
    response = TestClient(main.app).options(
        "/agents",
        headers={
            "Origin": "https://os.agno.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-private-network"] == "true"


def test_search_endpoint_waits_for_the_agent_result(monkeypatch: Any) -> None:
    async def fake_run(description: str) -> TalentSearchResult:
        assert "Python" in description
        return TalentSearchResult(
            status="completed",
            message="Busca concluída.",
            search_id="search_test",
            candidates_analyzed=2,
            google_sheet_url="https://docs.google.com/spreadsheets/d/test",
            excel_url="https://drive.google.com/open?id=test",
        )

    monkeypatch.setattr(main, "run_talent_agent", fake_run)
    response = TestClient(main.app).post(
        "/api/v1/talent/search",
        json={"description": "Pessoa desenvolvedora Python com FastAPI obrigatório."},
    )

    assert response.status_code == 200
    assert response.json()["candidates_analyzed"] == 2
    assert response.json()["status"] == "completed"


def test_plain_agent_question_fails_closed_as_clarification() -> None:
    result = main._coerce_agent_result("Qual é a função e quais critérios profissionais são obrigatórios?")

    assert result.status == "needs_clarification"
    assert result.candidates_analyzed == 0


def test_tool_result_wins_over_model_authored_placeholder_text() -> None:
    tool_result = TalentSearchResult(
        status="completed",
        message="Busca concluída.",
        search_id="search_structured",
        candidates_analyzed=4,
        google_sheet_url="https://docs.google.com/spreadsheets/d/test",
        excel_url="https://drive.google.com/open?id=test",
    )
    run = SimpleNamespace(
        content="**Resultado** | Candidato | [a ser extraído] |",
        tools=[
            SimpleNamespace(
                tool_name="search_talent_pool",
                tool_call_error=False,
                result=tool_result.model_dump_json(),
            )
        ],
    )

    result = main._coerce_agent_run(run)

    assert result.status == "completed"
    assert result.candidates_analyzed == 4
    assert result.search_id == "search_structured"


def test_candidate_cv_is_streamed_without_exposing_drive_url(monkeypatch: Any) -> None:
    class FakeStore:
        def get_profile_source(self, candidate_id: str) -> TalentProfileSource:
            assert candidate_id == "candidate-1"
            return TalentProfileSource(
                drive_file_id="drive-private-id",
                file_name="curriculo.pdf",
                mime_type="application/pdf",
                drive_url="https://drive.google.com/private",
            )

    class FakeWorkspace:
        async def download_resume(self, file: Any) -> bytes:
            assert file.drive_file_id == "drive-private-id"
            return b"%PDF-protected-content"

    monkeypatch.setattr(main, "get_cv_store", FakeStore)
    monkeypatch.setattr(main, "get_cv_workspace", FakeWorkspace)

    response = TestClient(main.app).get("/api/v1/talent/candidates/candidate-1/cv")

    assert response.status_code == 200
    assert response.content == b"%PDF-protected-content"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert "curriculo.pdf" in response.headers["content-disposition"]
    assert "drive.google.com" not in response.text


def test_xlsx_report_is_streamed_without_exposing_drive_url(monkeypatch: Any) -> None:
    class FakeStore:
        def get_search(self, search_id: str) -> dict[str, str]:
            assert search_id == "search-1"
            return {"excel_drive_file_id": "private-xlsx-id"}

    class FakeWorkspace:
        async def download_file(self, file_id: str) -> bytes:
            assert file_id == "private-xlsx-id"
            return b"xlsx-content"

    monkeypatch.setattr(main, "get_cv_store", FakeStore)
    monkeypatch.setattr(main, "get_cv_workspace", FakeWorkspace)

    response = TestClient(main.app).get("/api/v1/talent/searches/search-1/xlsx")

    assert response.status_code == 200
    assert response.content == b"xlsx-content"
    assert response.headers["content-type"] == main.XLSX_MIME
    assert 'filename="talent-search-search-1.xlsx"' in response.headers["content-disposition"]
    assert "drive.google.com" not in response.text


def test_agentos_agent_list_is_open_without_jwt() -> None:
    response = TestClient(main.app).get("/agents")

    assert response.status_code == 200
