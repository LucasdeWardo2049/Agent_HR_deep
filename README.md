# Talent Search Assistant

MVP local para RH analisar evidências profissionais em uma pasta de currículos. A aplicação sincroniza somente arquivos novos ou alterados, avalia todos os perfis contra critérios objetivos e gera um Google Sheets e um XLSX. Ela não ranqueia, aprova, rejeita nem recomenda candidatos.

```text
HTML/CSS/JS local
        ↓
FastAPI + AgentOS
        ↓
Talent Search Assistant (2 tools)
        ↓
TalentService + pesquisa pública de cargos
  ├── qwen-fast (conversa e ferramentas)
  ├── gpt-oss-120b (pipeline estruturado)
  ├── Composio (Search + Drive + Sheets)
  ├── SerpApi (fallback da pesquisa pública)
  ├── Gemini (fallback de PDF)
  └── Postgres (cache + histórico)
```

## Configuração

Copie [.env.example](.env.example) para `.env` e preencha:

- `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_API_KEY`, `AGENT_CHAT_MODEL` e `LOCAL_LLM_MODEL`;
- `GEMINI_API_KEY` e `GEMINI_PDF_MODEL`;
- `COMPOSIO_API_KEY`, `COMPOSIO_USER_ID`, `COMPOSIO_SEARCH_VERSION` e as duas pastas do Drive;
- `SERP_API_KEY` para complementar pesquisas públicas quando o Composio retornar menos de três fontes;
- `PUBLIC_APP_URL` com a origem pela qual os usuários alcançam a aplicação e os downloads;
- credenciais do Postgres, se forem diferentes dos padrões do Compose.

As variáveis antigas `OPENAI_BASE_URL`, `OPENAI_API_KEY` e `OPENAI_MODEL_ID` continuam aceitas como aliases. O `.env` real não é sobrescrito pela aplicação.

As conexões Google Drive e Google Sheets do usuário configurado no Composio precisam estar ativas. As ações são executadas diretamente com versões datadas dos toolkits.

## Executar

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

Abra [http://localhost:8000](http://localhost:8000). O AgentOS continua disponível com UI, MCP em `/mcp`, tracing e Slack quando suas duas variáveis estão configuradas. Este MVP local não usa JWT.

### Agent UI oficial para testar o AgentOS

O frontend oficial do Agno está em `agent-ui/` e aponta, por padrão, para este AgentOS em `http://localhost:8000`. Para iniciá-lo em outro terminal:

```powershell
cd agent-ui
npm.cmd install
npm.cmd run dev
```

Abra [http://localhost:3000](http://localhost:3000). Se o navegador já tiver salvo outro endpoint, clique em **Edit AgentOS** na barra lateral e informe `http://localhost:8000`. Não preencha token de autenticação: este MVP local está sem JWT. A UI cria um identificador anônimo persistente por navegador para isolar e reabrir somente as sessões daquele navegador.

Rotas do MVP:

- `GET /` — página local sem build frontend;
- `GET /health` — conectividade do Postgres e do modelo local;
- `POST /api/v1/talent/search` — busca síncrona pelo único agente.
- `GET /api/v1/talent/candidates/{candidateId}/cv` — transmite o currículo sem expor o link do Drive;
- `GET /api/v1/talent/searches/{searchId}/xlsx` — baixa o XLSX associado à busca sem expor o ID do Drive.

Exemplo:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/talent/search `
  -ContentType application/json `
  -Body '{"description":"Pessoa desenvolvedora Python com FastAPI obrigatório e PostgreSQL desejável."}'
```

## Processamento

Antes de cada busca, o serviço lista PDFs, DOCX e Google Docs na pasta configurada. O SHA-256 versionado é comparado ao cache em `talent_profiles`; arquivos inalterados não passam novamente pelo modelo, mas uma mudança no pipeline invalida o cache uma única vez. PDF e DOCX são extraídos localmente. Apenas PDFs com texto ruim ou estrutura inválida seguem para o Gemini.

Antes da avaliação, dados pessoais e contatos são removidos, educação e idiomas são normalizados, períodos explícitos de experiência são consolidados deterministicamente e o texto bruto é descartado. O avaliador recebe somente fatos profissionais estruturados e trechos curtos de evidência. Os relatórios usam as rotas da aplicação para CV e XLSX, nunca a URL original do Drive. Essas rotas ficam disponíveis para qualquer pessoa que alcance o AgentOS; mantenha o MVP em rede confiável ou use um gateway externo antes de expô-lo publicamente.

O texto dos currículos é tratado como conteúdo não confiável. Instruções embutidas são ignoradas, atributos protegidos não existem no schema e dados que começam como fórmulas são neutralizados antes de chegar às planilhas. O relatório mantém ordem alfabética, evidências, ausências e pontos que uma pessoa deve confirmar.

O domínio cria duas tabelas idempotentes no startup:

- `talent_profiles` — cache por arquivo/hash;
- `talent_searches` — vaga, avaliações, contagens e URLs.

O AgentOS mantém suas próprias tabelas operacionais para sessões, tracing e interfaces.

## Validação rápida

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements.txt pytest pytest-asyncio mypy ruff
.venv\Scripts\python.exe -m pytest
.venv\Scripts\ruff.exe check .
.venv\Scripts\mypy.exe . --config-file pyproject.toml
```

Os testes padrão são offline e usam fakes. O conjunto sintético faz chamadas somente ao modelo local:

```powershell
.venv\Scripts\python.exe -m evals
```

O E2E real é separado e opt-in. Ele cria uma pasta isolada, dois currículos sintéticos, o Sheets e o XLSX; no `finally`, remove somente esses IDs:

```powershell
$env:RUN_TALENT_E2E="1"
.venv\Scripts\python.exe -m pytest -m e2e tests/test_e2e_external.py
```

A solução só deve ser considerada integralmente validada quando esse E2E passar com as credenciais reais e os três nomes de abas forem confirmados: `Summary`, `Criteria` e `Candidates`.
