interface AgentThinkingLoaderProps {
  label?: string
}

const AgentThinkingLoader = ({
  label = 'Preparando resposta'
}: AgentThinkingLoaderProps) => (
  <div className="flex items-center justify-center gap-2 text-sm text-primary/60">
    <div className="flex gap-1" aria-hidden="true">
      <div className="size-2 animate-bounce rounded-full bg-primary/20 [animation-delay:-0.3s] [animation-duration:0.70s]" />
      <div className="size-2 animate-bounce rounded-full bg-primary/20 [animation-delay:-0.10s] [animation-duration:0.70s]" />
      <div className="size-2 animate-bounce rounded-full bg-primary/20 [animation-duration:0.70s]" />
    </div>
    <span>{label}</span>
  </div>
)

export default AgentThinkingLoader
