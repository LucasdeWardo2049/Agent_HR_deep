from pathlib import Path
from typing import Any

import pytest

from app.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceError
from app.schemas import CandidateAssessment, CandidateProfile, CriterionAssessment, JobCriterion, JobProfile
from app.settings import Settings


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        local_llm_api_key="test",
        composio_api_key="test",
        composio_user_id="user",
        google_drive_talent_pool_folder_id="pool",
        results_drive_folder_id="results",
    )


class FakeSession:
    def __init__(self) -> None:
        self.request: dict[str, Any] = {}

    def execute(self, tool_slug: str, *, arguments: dict[str, Any]) -> dict[str, Any]:
        self.request = {"tool_slug": tool_slug, "arguments": arguments}
        return {"data": {"id": "ok"}, "error": None, "log_id": "log-test"}


class FakeClient:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.request: dict[str, Any] = {}

    def create(self, **request: Any) -> FakeSession:
        self.request = request
        return self.session


def test_session_execution_scopes_identity_and_captures_log_id() -> None:
    workspace = GoogleWorkspaceClient(_settings())
    session = FakeSession()
    client = FakeClient(session)
    workspace.client = client

    workspace._execute("GOOGLEDRIVE_FIND_FILE", {"q": "trashed = false"}, toolkit="googledrive")

    assert client.request == {
        "user_id": "user",
        "toolkits": ["googledrive", "googlesheets"],
        "manage_connections": False,
        "sandbox": {"enable": False},
    }
    assert session.request == {
        "tool_slug": "GOOGLEDRIVE_FIND_FILE",
        "arguments": {"q": "trashed = false"},
    }
    assert workspace.last_log_id == "log-test"


def test_session_connection_error_preserves_request_id() -> None:
    class ConnectionErrorSession:
        def execute(self, tool_slug: str, *, arguments: dict[str, Any]) -> dict[str, Any]:
            del tool_slug, arguments
            error = RuntimeError("connection missing")
            error.body = {  # type: ignore[attr-defined]
                "error": {
                    "slug": "ToolRouterV2_NoActiveConnection",
                    "request_id": "request-test",
                }
            }
            raise error

    workspace = GoogleWorkspaceClient(_settings())
    workspace.session = ConnectionErrorSession()

    with pytest.raises(GoogleWorkspaceError, match="Nenhuma conexão Google ativa"):
        workspace._execute("GOOGLEDRIVE_FIND_FILE", {"q": "trashed = false"}, toolkit="googledrive")

    assert workspace.last_request_id == "request-test"


async def test_report_uses_exact_three_sheet_names_and_deletes_temporary_xlsx(monkeypatch: Any) -> None:
    workspace = GoogleWorkspaceClient.__new__(GoogleWorkspaceClient)
    workspace.settings = _settings()
    calls: list[tuple[str, dict[str, Any], str]] = []
    uploaded_path: Path | None = None

    def stage_file(path: Path, *, slug: str, toolkit: str) -> dict[str, str]:
        nonlocal uploaded_path
        uploaded_path = path
        assert uploaded_path.is_file()
        assert slug == "GOOGLEDRIVE_UPLOAD_FILE"
        assert toolkit == "googledrive"
        return {
            "name": path.name,
            "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "s3key": "staged/xlsx-1",
        }

    def execute(slug: str, arguments: dict[str, Any], *, toolkit: str) -> dict[str, Any]:
        calls.append((slug, arguments, toolkit))
        if slug == "GOOGLESHEETS_CREATE_GOOGLE_SHEET1":
            return {
                "spreadsheetId": "sheet-1",
                "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/sheet-1",
                "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}],
            }
        if slug == "GOOGLEDRIVE_UPLOAD_FILE":
            assert arguments["file_to_upload"]["s3key"] == "staged/xlsx-1"
            return {"id": "xlsx-1", "webViewLink": "https://drive.google.com/open?id=xlsx-1"}
        return {}

    monkeypatch.setattr(workspace, "_stage_file", stage_file)
    monkeypatch.setattr(workspace, "_execute", execute)
    job = JobProfile(
        title="Backend",
        criteria=[JobCriterion(id="python", description="Python", required=True, criterion_type="skill")],
    )
    profile = CandidateProfile(
        candidate_id="cv-1",
        full_name="Ana",
        source_drive_file_id="cv-1",
        source_drive_url="https://drive.test/cv-1",
    )
    assessment = CandidateAssessment(
        candidate_id="cv-1",
        candidate_name="Ana",
        criteria=[CriterionAssessment(criterion_id="python", status="not_found")],
        required_total=1,
    )

    artifacts = await workspace.create_report(job, [assessment], [profile])

    assert artifacts.excel_url.endswith("xlsx-1")
    assert artifacts.excel_file_id == "xlsx-1"
    assert uploaded_path is not None and not uploaded_path.exists()
    renames = [arguments for slug, arguments, _ in calls if slug == "GOOGLESHEETS_UPDATE_SHEET_PROPERTIES"]
    assert renames[0]["updateSheetProperties"]["properties"]["title"] == "Summary"
    added = [arguments["properties"]["title"] for slug, arguments, _ in calls if slug == "GOOGLESHEETS_ADD_SHEET"]
    assert added == ["Criteria", "Candidates"]
    written = [arguments["sheet_name"] for slug, arguments, _ in calls if slug == "GOOGLESHEETS_BATCH_UPDATE"]
    assert written == ["Summary", "Criteria", "Candidates"]
    link_formats = [arguments for slug, arguments, _ in calls if slug == "GOOGLESHEETS_FORMAT_CELL"]
    assert link_formats == []
    candidates_write = next(
        arguments for slug, arguments, _ in calls
        if slug == "GOOGLESHEETS_BATCH_UPDATE" and arguments["sheet_name"] == "Candidates"
    )
    assert candidates_write["values"][1][1] == (
        "http://localhost:8000/api/v1/talent/candidates/cv-1/cv"
    )
