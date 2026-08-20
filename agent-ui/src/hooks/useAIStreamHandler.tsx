import { useCallback } from 'react'
import { useQueryState } from 'nuqs'

import { APIRoutes } from '@/api/routes'
import { constructEndpointUrl } from '@/lib/constructEndpointUrl'
import { getJsonMarkdown } from '@/lib/utils'
import { useStore } from '@/store'
import {
  RunEvent,
  type ChatMessage,
  type RunResponse,
  type RunResponseContent,
  type ToolCall
} from '@/types/os'

import useAIResponseStream from './useAIResponseStream'
import useChatActions from './useChatActions'

function updateClientMessage(
  messages: ChatMessage[],
  clientId: string,
  update: (message: ChatMessage) => ChatMessage
): ChatMessage[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.client_id === clientId && message.role === 'agent') {
      const next = [...messages]
      next[index] = update(message)
      return next
    }
  }
  // The user may have opened another session while this request was active.
  return messages
}

const useAIChatStreamHandler = () => {
  const setMessages = useStore((state) => state.setMessages)
  const { focusChatInput } = useChatActions()
  const [agentId] = useQueryState('agent')
  const [teamId] = useQueryState('team')
  const [sessionId, setSessionId] = useQueryState('session')
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const authToken = useStore((state) => state.authToken)
  const userId = useStore((state) => state.userId)
  const mode = useStore((state) => state.mode)
  const setStreamingErrorMessage = useStore(
    (state) => state.setStreamingErrorMessage
  )
  const setIsStreaming = useStore((state) => state.setIsStreaming)
  const setSessionsData = useStore((state) => state.setSessionsData)
  const { streamResponse } = useAIResponseStream()

  const processToolCall = useCallback(
    (toolCall: ToolCall, previous: ToolCall[] = []) => {
      const toolCallId =
        toolCall.tool_call_id || `${toolCall.tool_name}-${toolCall.created_at}`
      const existingIndex = previous.findIndex(
        (item) =>
          (item.tool_call_id && item.tool_call_id === toolCall.tool_call_id) ||
          (!item.tool_call_id &&
            toolCall.tool_name &&
            toolCall.created_at &&
            `${item.tool_name}-${item.created_at}` === toolCallId)
      )

      if (existingIndex === -1) return [...previous, toolCall]
      const updated = [...previous]
      updated[existingIndex] = { ...updated[existingIndex], ...toolCall }
      return updated
    },
    []
  )

  const processChunkToolCalls = useCallback(
    (
      chunk: RunResponseContent | RunResponse,
      existingToolCalls: ToolCall[] = []
    ) => {
      let updated = [...existingToolCalls]
      if (chunk.tool) updated = processToolCall(chunk.tool, updated)
      for (const toolCall of chunk.tools ?? []) {
        updated = processToolCall(toolCall, updated)
      }
      return updated
    },
    [processToolCall]
  )

  const handleStreamResponse = useCallback(
    async (input: string | FormData) => {
      setIsStreaming(true)
      setStreamingErrorMessage('')

      const formData = input instanceof FormData ? input : new FormData()
      if (typeof input === 'string') formData.append('message', input)

      const clientRunId =
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random()}`
      const userContent = String(formData.get('message') ?? '')
      const createdAt = Math.floor(Date.now() / 1000)

      setMessages((previous) => {
        let base = previous
        if (previous.length >= 2) {
          const last = previous.at(-1)
          const penultimate = previous.at(-2)
          if (
            last?.role === 'agent' &&
            last.streamingError &&
            penultimate?.role === 'user'
          ) {
            base = previous.slice(0, -2)
          }
        }
        return [
          ...base,
          { role: 'user', content: userContent, created_at: createdAt },
          {
            role: 'agent',
            content: '',
            tool_calls: [],
            streamingError: false,
            created_at: createdAt + 1,
            client_id: clientRunId
          }
        ]
      })

      const updateAgentMessage = (update: (message: ChatMessage) => ChatMessage) => {
        setMessages((messages) => updateClientMessage(messages, clientRunId, update))
      }
      const markError = (message: string) => {
        setStreamingErrorMessage(message)
        updateAgentMessage((current) => ({
          ...current,
          streamingError: true
        }))
      }

      let newSessionId = sessionId
      try {
        const endpointUrl = constructEndpointUrl(selectedEndpoint)
        let runUrl: string | null = null

        if (mode === 'team' && teamId) {
          runUrl = APIRoutes.TeamRun(endpointUrl, teamId)
        } else if (mode === 'agent' && agentId) {
          runUrl = APIRoutes.AgentRun(endpointUrl).replace(
            '{agent_id}',
            agentId
          )
        }

        if (!runUrl) {
          markError('Selecione um agente ou time antes de enviar a mensagem.')
          return
        }

        formData.append('stream', 'true')
        formData.append('session_id', sessionId ?? '')
        formData.set('user_id', userId)

        const headers: Record<string, string> = {}
        if (authToken) headers.Authorization = `Bearer ${authToken}`

        await streamResponse({
          apiUrl: runUrl,
          headers,
          requestBody: formData,
          onChunk: (chunk: RunResponse) => {
            if (
              chunk.event === RunEvent.RunStarted ||
              chunk.event === RunEvent.TeamRunStarted ||
              chunk.event === RunEvent.ReasoningStarted ||
              chunk.event === RunEvent.TeamReasoningStarted
            ) {
              newSessionId = chunk.session_id as string
              setSessionId(chunk.session_id as string)
              if (
                (!sessionId || sessionId !== chunk.session_id) &&
                chunk.session_id
              ) {
                const sessionData = {
                  session_id: chunk.session_id,
                  session_name: userContent,
                  created_at: chunk.created_at
                }
                setSessionsData((sessions) => {
                  if (sessions?.some((item) => item.session_id === chunk.session_id)) {
                    return sessions
                  }
                  return [sessionData, ...(sessions ?? [])]
                })
              }
              return
            }

            if (
              chunk.event === RunEvent.ToolCallStarted ||
              chunk.event === RunEvent.TeamToolCallStarted ||
              chunk.event === RunEvent.ToolCallCompleted ||
              chunk.event === RunEvent.TeamToolCallCompleted
            ) {
              updateAgentMessage((current) => ({
                ...current,
                tool_calls: processChunkToolCalls(chunk, current.tool_calls)
              }))
              return
            }

            if (chunk.event === RunEvent.CustomEvent) {
              if (typeof chunk.label === 'string' && typeof chunk.phase === 'string') {
                updateAgentMessage((current) => ({
                  ...current,
                  progress: {
                    phase: chunk.phase,
                    label: chunk.label,
                    current:
                      typeof chunk.current === 'number' ? chunk.current : undefined,
                    total: typeof chunk.total === 'number' ? chunk.total : undefined
                  }
                }))
              }
              return
            }

            if (
              chunk.event === RunEvent.RunContent ||
              chunk.event === RunEvent.TeamRunContent
            ) {
              updateAgentMessage((current) => {
                if (typeof chunk.content === 'string') {
                  return {
                    ...current,
                    content: current.content + chunk.content,
                    tool_calls: processChunkToolCalls(
                      chunk,
                      current.tool_calls
                    ),
                    created_at: chunk.created_at ?? current.created_at,
                    images: chunk.images ?? current.images,
                    videos: chunk.videos ?? current.videos,
                    audio: chunk.audio ?? current.audio,
                    extra_data: {
                      ...current.extra_data,
                      reasoning_steps:
                        chunk.extra_data?.reasoning_steps ??
                        current.extra_data?.reasoning_steps,
                      references:
                        chunk.extra_data?.references ??
                        current.extra_data?.references
                    }
                  }
                }

                if (chunk.content !== null && chunk.content !== undefined) {
                  return {
                    ...current,
                    content: current.content + getJsonMarkdown(chunk.content)
                  }
                }

                const transcript = chunk.response_audio?.transcript
                if (typeof transcript === 'string') {
                  return {
                    ...current,
                    response_audio: {
                      ...current.response_audio,
                      transcript:
                        (current.response_audio?.transcript ?? '') + transcript
                    }
                  }
                }
                return current
              })
              return
            }

            if (
              chunk.event === RunEvent.ReasoningStep ||
              chunk.event === RunEvent.TeamReasoningStep
            ) {
              updateAgentMessage((current) => ({
                ...current,
                extra_data: {
                  ...current.extra_data,
                  reasoning_steps: [
                    ...(current.extra_data?.reasoning_steps ?? []),
                    ...(chunk.extra_data?.reasoning_steps ?? [])
                  ]
                }
              }))
              return
            }

            if (
              chunk.event === RunEvent.ReasoningCompleted ||
              chunk.event === RunEvent.TeamReasoningCompleted
            ) {
              if (chunk.extra_data?.reasoning_steps) {
                updateAgentMessage((current) => ({
                  ...current,
                  extra_data: {
                    ...current.extra_data,
                    reasoning_steps: chunk.extra_data?.reasoning_steps
                  }
                }))
              }
              return
            }

            if (
              chunk.event === RunEvent.RunError ||
              chunk.event === RunEvent.TeamRunError ||
              chunk.event === RunEvent.TeamRunCancelled
            ) {
              const errorContent =
                (chunk.content as string) ||
                (chunk.event === RunEvent.TeamRunCancelled
                  ? 'A execução foi cancelada.'
                  : 'Ocorreu um erro durante a execução.')
              markError(errorContent)
              if (newSessionId) {
                setSessionsData(
                  (sessions) =>
                    sessions?.filter(
                      (item) => item.session_id !== newSessionId
                    ) ?? null
                )
              }
              return
            }

            if (
              chunk.event === RunEvent.RunCompleted ||
              chunk.event === RunEvent.TeamRunCompleted
            ) {
              updateAgentMessage((current) => {
                let content: string
                if (typeof chunk.content === 'string') {
                  content = chunk.content
                } else {
                  try {
                    content = JSON.stringify(chunk.content)
                  } catch {
                    content = 'Não foi possível interpretar a resposta.'
                  }
                }
                return {
                  ...current,
                  content,
                  tool_calls: processChunkToolCalls(chunk, current.tool_calls),
                  images: chunk.images ?? current.images,
                  videos: chunk.videos ?? current.videos,
                  response_audio: chunk.response_audio,
                  created_at: chunk.created_at ?? current.created_at,
                  extra_data: {
                    reasoning_steps:
                      chunk.extra_data?.reasoning_steps ??
                      current.extra_data?.reasoning_steps,
                    references:
                      chunk.extra_data?.references ??
                      current.extra_data?.references
                  }
                }
              })
            }
          },
          onError: (error) => {
            markError(error.message)
            if (newSessionId) {
              setSessionsData(
                (sessions) =>
                  sessions?.filter(
                    (item) => item.session_id !== newSessionId
                  ) ?? null
              )
            }
          },
          onComplete: () => undefined
        })
      } catch (error) {
        markError(error instanceof Error ? error.message : String(error))
        if (newSessionId) {
          setSessionsData(
            (sessions) =>
              sessions?.filter((item) => item.session_id !== newSessionId) ??
              null
          )
        }
      } finally {
        focusChatInput()
        setIsStreaming(false)
      }
    },
    [
      agentId,
      authToken,
      focusChatInput,
      mode,
      processChunkToolCalls,
      selectedEndpoint,
      sessionId,
      setIsStreaming,
      setMessages,
      setSessionId,
      setSessionsData,
      setStreamingErrorMessage,
      streamResponse,
      teamId,
      userId
    ]
  )

  return { handleStreamResponse }
}

export default useAIChatStreamHandler
