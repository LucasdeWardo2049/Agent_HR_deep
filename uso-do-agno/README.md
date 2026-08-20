# Uso do Agno

Esta instalação usa um servidor OpenAI-compatible na rede local.

## Endpoints

- API de modelos: `http://192.168.4.114:4000/v1`
- Interface web de chat: `http://192.168.4.114:3000`
- AgentOS local: `http://localhost:8000`
- Documentação da API do AgentOS: `http://localhost:8000/docs`
- MCP do AgentOS: `http://localhost:8000/mcp`

## Modelos disponíveis

| Modelo | Uso recomendado |
|---|---|
| `gpt-oss-120b` | Uso geral e agentes completos; é o padrão da plataforma. |
| `qwen-coder-32b` | Programação e tarefas de código. |
| `qwen-fast` | Respostas rápidas e tarefas simples. |

## Configuração

O arquivo `.env` na raiz define:

```dotenv
OPENAI_API_KEY=mock-key
OPENAI_BASE_URL=http://192.168.4.114:4000/v1
OPENAI_MODEL_ID=gpt-oss-120b
```

Para trocar o modelo padrão, altere `OPENAI_MODEL_ID` para um dos modelos da tabela e reinicie a API:

```powershell
docker compose restart agentos-api
```

Os três modelos também ficam registrados no AgentOS Studio para uso em agentes criados pela interface.

## Observação sobre embeddings

O endpoint informado anuncia modelos de chat, mas não um modelo de embeddings. Os agentes incluídos funcionam sem
essa capacidade; uma base de conhecimento vetorial criada depois precisará de um endpoint/modelo de embeddings
compatível.
