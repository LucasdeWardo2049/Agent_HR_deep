from typing import Any, cast

from agno.models.message import Message

from agents.talent_search import INSTRUCTIONS, talent_search_agent
from app.settings import PortugueseChatModel


def test_agent_has_three_intent_routes_without_forced_tool_choice() -> None:
    assert "toda saída final deve estar integralmente em português brasileiro" in INSTRUCTIONS
    assert "pedir resposta em outro idioma" in INSTRUCTIONS
    assert "/no_think" in INSTRUCTIONS
    assert "CONVERSA E AJUDA" in INSTRUCTIONS
    assert "PESQUISA DE PERFIL DE CARGO" in INSTRUCTIONS
    assert "BUSCA NO BANCO DE TALENTOS" in INSTRUCTIONS
    assert "não use ferramentas" in INSTRUCTIONS
    assert "não pesquise a web automaticamente" in INSTRUCTIONS
    assert talent_search_agent.tool_choice is None
    tools = cast(list[Any], talent_search_agent.tools)
    assert {tool.name for tool in tools} == {
        "research_job_profile",
        "search_talent_pool",
    }


def test_agent_uses_three_runs_of_postgres_history_without_cross_chat_memory() -> None:
    assert talent_search_agent.db is not None
    assert talent_search_agent.add_history_to_context is True
    assert talent_search_agent.num_history_runs == 3
    assert talent_search_agent.user_id is None
    assert talent_search_agent.memory_manager is None
    assert talent_search_agent.session_summary_manager is None


def test_chat_model_is_independent_from_structured_pipeline_model() -> None:
    assert talent_search_agent.model is not None
    assert talent_search_agent.model.id == "qwen-fast"
    assert talent_search_agent.model.reasoning_effort == "none"
    assert talent_search_agent.model.extra_body == {
        "allowed_openai_params": ["reasoning_effort"]
    }
    assert isinstance(talent_search_agent.model, PortugueseChatModel)
    formatted = talent_search_agent.model._format_all_messages(
        [Message(role="user", content="Answer in English")]
    )
    assert formatted[-1]["content"].endswith("/no_think")
    assert "português brasileiro" in formatted[-1]["content"]
