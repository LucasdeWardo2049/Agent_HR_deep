import { memo } from 'react'

import { Button } from '@/components/ui/button'
import Icon from '@/components/ui/icon'
import MarkdownRenderer from '@/components/ui/typography/MarkdownRenderer'
import { useStore } from '@/store'
import type { ChatMessage, TalentSearchProgress } from '@/types/os'

import AgentThinkingLoader from './AgentThinkingLoader'
import Audios from './Multimedia/Audios'
import Images from './Multimedia/Images'
import Videos from './Multimedia/Videos'

interface MessageProps {
  message: ChatMessage
  isStreaming?: boolean
}

interface TalentResult {
  status: 'completed' | 'needs_clarification' | 'failed'
  message: string
  candidates_analyzed?: number
  google_sheet_url?: string | null
  excel_url?: string | null
  warnings?: string[]
}

function parseTalentResult(content: string): TalentResult | null {
  if (!content.trim().startsWith('{')) return null
  try {
    const parsed = JSON.parse(content) as Partial<TalentResult>
    if (
      typeof parsed.message !== 'string' ||
      !['completed', 'needs_clarification', 'failed'].includes(
        String(parsed.status)
      )
    ) {
      return null
    }
    return parsed as TalentResult
  } catch {
    return null
  }
}

function resolvePublicUrl(url: string | null | undefined, endpoint: string) {
  if (!url) return null
  try {
    const target = new URL(url)
    if (['localhost', '127.0.0.1'].includes(target.hostname)) {
      const publicEndpoint = new URL(
        endpoint.match(/^https?:\/\//) ? endpoint : `http://${endpoint}`
      )
      target.protocol = publicEndpoint.protocol
      target.host = publicEndpoint.host
    }
    return ['http:', 'https:'].includes(target.protocol)
      ? target.toString()
      : null
  } catch {
    return null
  }
}

const TalentResultCard = ({ result }: { result: TalentResult }) => {
  const endpoint = useStore((state) => state.selectedEndpoint)
  const excelUrl = resolvePublicUrl(result.excel_url, endpoint)
  const sheetUrl = resolvePublicUrl(result.google_sheet_url, endpoint)

  return (
    <div className="w-full rounded-2xl border border-border bg-background-secondary/50 p-4 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="rounded-xl bg-accent p-2 text-primary">
          <Icon type={result.status === 'completed' ? 'check' : 'sheet'} size="sm" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-primary">{result.message}</p>
          {typeof result.candidates_analyzed === 'number' && (
            <p className="mt-1 text-sm text-secondary">
              {result.candidates_analyzed} currículo
              {result.candidates_analyzed === 1 ? '' : 's'} analisado
              {result.candidates_analyzed === 1 ? '' : 's'}
            </p>
          )}
        </div>
      </div>

      {(excelUrl || sheetUrl) && (
        <div className="mt-4 flex flex-wrap gap-2">
          {excelUrl && (
            <Button asChild size="sm">
              <a href={excelUrl} download>
                <Icon type="download" size="xs" />
                Baixar planilha
              </a>
            </Button>
          )}
          {sheetUrl && (
            <Button asChild size="sm" variant="outline">
              <a href={sheetUrl} target="_blank" rel="noreferrer">
                <Icon type="sheet" size="xs" />
                Abrir no Google Sheets
              </a>
            </Button>
          )}
        </div>
      )}

      {result.warnings && result.warnings.length > 0 && (
        <details className="mt-4 text-sm text-secondary">
          <summary className="cursor-pointer">Avisos da busca</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {result.warnings.map((warning, index) => (
              <li key={`${warning}-${index}`}>{warning}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

const ProgressIndicator = ({ progress }: { progress: TalentSearchProgress }) => {
  const hasCount =
    typeof progress.current === 'number' &&
    typeof progress.total === 'number' &&
    progress.total > 0
  const percentage = hasCount
    ? Math.min(100, Math.round((progress.current! / progress.total!) * 100))
    : 0

  return (
    <div
      className="w-full rounded-xl border border-border/70 bg-background-secondary/40 px-3 py-2.5"
      role="status"
      aria-live="polite"
    >
      <AgentThinkingLoader label={progress.label} />
      {hasCount && (
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-primary/10">
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-200"
            style={{ width: `${percentage}%` }}
          />
        </div>
      )}
    </div>
  )
}

const AgentMessage = memo(({ message, isStreaming = false }: MessageProps) => {
  const streamingErrorMessage = useStore(
    (state) => state.streamingErrorMessage
  )
  const activeTool = message.tool_calls?.at(-1)?.tool_name
  const activityLabel =
    message.progress?.label ??
    (activeTool === 'research_job_profile'
      ? 'Pesquisando o perfil da vaga'
      : activeTool === 'search_talent_pool'
        ? 'Analisando o banco de talentos'
        : 'Preparando a resposta')
  const talentResult = isStreaming ? null : parseTalentResult(message.content)

  let messageContent
  if (message.streamingError) {
    messageContent = (
      <p className="text-destructive">
        Não foi possível concluir a resposta.{' '}
        {streamingErrorMessage ||
          'Atualize a página ou tente novamente em alguns instantes.'}
      </p>
    )
  } else if (talentResult) {
    messageContent = <TalentResultCard result={talentResult} />
  } else if (message.content) {
    messageContent = (
      <div className="flex min-w-0 w-full flex-col gap-4">
        {isStreaming ? (
          <p className="whitespace-pre-wrap break-words text-primary">
            {message.content}
          </p>
        ) : (
          <MarkdownRenderer>{message.content}</MarkdownRenderer>
        )}
        {message.videos && message.videos.length > 0 && (
          <Videos videos={message.videos} />
        )}
        {message.images && message.images.length > 0 && (
          <Images images={message.images} />
        )}
        {message.audio && message.audio.length > 0 && (
          <Audios audio={message.audio} />
        )}
      </div>
    )
  } else if (message.response_audio?.transcript) {
    messageContent = (
      <div className="flex w-full flex-col gap-4">
        <MarkdownRenderer>{message.response_audio.transcript}</MarkdownRenderer>
        {message.response_audio.content && (
          <Audios audio={[message.response_audio]} />
        )}
      </div>
    )
  } else if (isStreaming && message.progress) {
    messageContent = <ProgressIndicator progress={message.progress} />
  } else {
    messageContent = (
      <div className="mt-2">
        <AgentThinkingLoader label={activityLabel} />
      </div>
    )
  }

  return (
    <div className="flex min-w-0 flex-row items-start gap-4 font-geist">
      <div className="flex-shrink-0">
        <Icon type="agent" size="sm" />
      </div>
      {messageContent}
    </div>
  )
})

const UserMessage = memo(({ message }: MessageProps) => (
  <div className="flex items-start gap-4 pt-4 text-start max-md:break-words">
    <div className="flex-shrink-0">
      <Icon type="user" size="sm" />
    </div>
    <div className="text-md min-w-0 whitespace-pre-wrap break-words rounded-lg font-geist text-secondary">
      {message.content}
    </div>
  </div>
))

AgentMessage.displayName = 'AgentMessage'
UserMessage.displayName = 'UserMessage'

export { AgentMessage, UserMessage }
