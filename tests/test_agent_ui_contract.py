from pathlib import Path

ROOT = Path(__file__).parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_browser_user_id_is_persisted_and_sent_to_runs_and_sessions() -> None:
    store = read("agent-ui/src/store.ts")
    stream = read("agent-ui/src/hooks/useAIStreamHandler.tsx")
    api = read("agent-ui/src/api/os.ts")

    assert "talent-ui-${crypto.randomUUID()}" in store
    assert "userId: state.userId" in store
    assert "formData.set('user_id', userId)" in stream
    assert api.count("'user_id', userId") == 4


def test_ui_parallelizes_discovery_and_batches_content_per_frame() -> None:
    actions = read("agent-ui/src/hooks/useChatActions.ts")
    stream = read("agent-ui/src/hooks/useAIResponseStream.tsx")
    messages = read("agent-ui/src/components/chat/ChatArea/Messages/MessageItem.tsx")

    assert "Promise.all([getTeams(), getAgents()])" in actions
    assert "window.requestAnimationFrame(flushPendingChunks)" in stream
    assert "Pesquisando perfil da vaga" in messages
    assert "Analisando banco de talentos" in messages
    assert "pending.content + incoming.content" in stream
    assert "pendingProgressChunk = chunk" in stream
    assert "replace(lastContent" not in read("agent-ui/src/hooks/useAIStreamHandler.tsx")


def test_ui_handles_progress_cancellation_and_owns_the_scroll_container() -> None:
    stream_handler = read("agent-ui/src/hooks/useAIStreamHandler.tsx")
    message_area = read("agent-ui/src/components/chat/ChatArea/MessageArea.tsx")
    chat_area = read("agent-ui/src/components/chat/ChatArea/ChatArea.tsx")

    assert "RunEvent.CustomEvent" in stream_handler
    assert "RunEvent.RunCancelled" in stream_handler
    assert "createdSessionId" in stream_handler
    assert "scrollClassName=" in message_area
    assert "overflow-y-auto" in message_area
    assert "min-h-0" in chat_area
    assert "overflow-hidden" in chat_area
