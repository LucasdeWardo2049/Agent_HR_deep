import { RunEvent, type RunResponseContent } from '@/types/os'
import { useCallback } from 'react'

interface NewFormatData {
  event: string
  data: string | Record<string, unknown>
}

type LegacyEventFormat = RunResponseContent & { event: string }

function isLegacyFormat(data: unknown): data is LegacyEventFormat {
  return (
    typeof data === 'object' &&
    data !== null &&
    'event' in data &&
    !('data' in data) &&
    typeof data.event === 'string'
  )
}

function convertNewFormatToLegacy(data: NewFormatData): LegacyEventFormat {
  let parsedData: Record<string, unknown>
  if (typeof data.data === 'string') {
    try {
      parsedData = JSON.parse(data.data) as Record<string, unknown>
    } catch {
      parsedData = {}
    }
  } else {
    parsedData = data.data
  }

  return { event: data.event, ...parsedData } as LegacyEventFormat
}

function normalizeEvent(
  payload: unknown,
  sseEvent: string | undefined
): RunResponseContent | null {
  if (isLegacyFormat(payload)) return payload

  if (
    typeof payload === 'object' &&
    payload !== null &&
    'event' in payload &&
    'data' in payload
  ) {
    return convertNewFormatToLegacy(payload as NewFormatData)
  }

  if (typeof payload === 'object' && payload !== null && sseEvent) {
    const record = payload as Record<string, unknown>
    if (sseEvent === 'error') {
      return {
        ...record,
        event: RunEvent.RunError,
        content: record.detail ?? record.message ?? 'Erro durante a execução'
      } as RunResponseContent
    }
    return { ...record, event: sseEvent } as RunResponseContent
  }

  return null
}

/** Incremental SSE parser. It scans each received character only once. */
function createSSEParser(
  onChunk: (chunk: RunResponseContent) => void
): { feed: (text: string) => void; finish: () => void } {
  let lineBuffer = ''
  let newlineSearchFrom = 0
  let eventName: string | undefined
  let dataLines: string[] = []

  const emitFrame = () => {
    if (dataLines.length === 0) {
      eventName = undefined
      return
    }

    const rawData = dataLines.join('\n')
    dataLines = []
    try {
      const normalized = normalizeEvent(JSON.parse(rawData), eventName)
      if (normalized) onChunk(normalized)
    } finally {
      eventName = undefined
    }
  }

  const processLine = (rawLine: string) => {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (line === '') {
      emitFrame()
      return
    }
    if (line.startsWith(':')) return

    const separator = line.indexOf(':')
    const field = separator === -1 ? line : line.slice(0, separator)
    let value = separator === -1 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') eventName = value
    if (field === 'data') dataLines.push(value)
  }

  const feed = (text: string) => {
    lineBuffer += text
    while (true) {
      const newlineIndex = lineBuffer.indexOf('\n', newlineSearchFrom)
      if (newlineIndex === -1) {
        newlineSearchFrom = Math.max(0, lineBuffer.length - 1)
        return
      }
      processLine(lineBuffer.slice(0, newlineIndex))
      lineBuffer = lineBuffer.slice(newlineIndex + 1)
      newlineSearchFrom = 0
    }
  }

  const finish = () => {
    if (lineBuffer) processLine(lineBuffer)
    lineBuffer = ''
    newlineSearchFrom = 0
    emitFrame()
  }

  return { feed, finish }
}

function mergeContentChunks(
  pending: RunResponseContent | null,
  incoming: RunResponseContent
): RunResponseContent | null {
  if (typeof incoming.content !== 'string') return null
  if (!pending || pending.event !== incoming.event) return incoming
  if (typeof pending.content !== 'string') return incoming

  return {
    ...pending,
    ...incoming,
    content: pending.content + incoming.content
  }
}

export default function useAIResponseStream() {
  const streamResponse = useCallback(
    async (options: {
      apiUrl: string
      headers?: Record<string, string>
      requestBody: FormData | Record<string, unknown>
      onChunk: (chunk: RunResponseContent) => void
      onError: (error: Error) => void
      onComplete: () => void
    }): Promise<void> => {
      const {
        apiUrl,
        headers = {},
        requestBody,
        onChunk,
        onError,
        onComplete
      } = options

      let pendingContentChunk: RunResponseContent | null = null
      let pendingProgressChunk: RunResponseContent | null = null
      let frameId: number | null = null

      const flushPendingChunks = () => {
        if (frameId !== null) {
          window.cancelAnimationFrame(frameId)
          frameId = null
        }
        if (pendingContentChunk) {
          onChunk(pendingContentChunk)
          pendingContentChunk = null
        }
        if (pendingProgressChunk) {
          onChunk(pendingProgressChunk)
          pendingProgressChunk = null
        }
      }

      const scheduleFlush = () => {
        if (frameId === null) {
          frameId = window.requestAnimationFrame(flushPendingChunks)
        }
      }

      const emitChunk = (chunk: RunResponseContent) => {
        const isTextDelta =
          (chunk.event === RunEvent.RunContent ||
            chunk.event === RunEvent.TeamRunContent) &&
          typeof chunk.content === 'string'

        if (isTextDelta) {
          if (pendingProgressChunk) flushPendingChunks()
          // RunContent is a delta in Agno 2.8.5. Preserve every delta received
          // during a frame, then commit only once to React.
          pendingContentChunk = mergeContentChunks(pendingContentChunk, chunk)
          scheduleFlush()
          return
        }

        if (chunk.event === RunEvent.CustomEvent) {
          if (pendingContentChunk) flushPendingChunks()
          pendingProgressChunk = chunk
          scheduleFlush()
          return
        }

        flushPendingChunks()
        onChunk(chunk)
      }

      try {
        const response = await fetch(apiUrl, {
          method: 'POST',
          headers: {
            Accept: 'text/event-stream',
            ...(!(requestBody instanceof FormData) && {
              'Content-Type': 'application/json'
            }),
            ...headers
          },
          body:
            requestBody instanceof FormData
              ? requestBody
              : JSON.stringify(requestBody)
        })

        if (!response.ok) {
          const errorData = (await response.json()) as { detail?: unknown }
          throw new Error(
            typeof errorData.detail === 'string'
              ? errorData.detail
              : `A API respondeu com status ${response.status}`
          )
        }
        if (!response.body) {
          throw new Error('A resposta da API não contém um stream.')
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        const parser = createSSEParser(emitChunk)

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          parser.feed(decoder.decode(value, { stream: true }))
        }
        parser.feed(decoder.decode())
        parser.finish()
        flushPendingChunks()
        onComplete()
      } catch (error) {
        flushPendingChunks()
        onError(error instanceof Error ? error : new Error(String(error)))
      }
    },
    []
  )

  return { streamResponse }
}
