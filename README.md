# AgentOS — one backend for every frontend

An agent server that attaches to any client. Built on [Agno](https://docs.agno.com), everything runs in your cloud and your data lives in your database.

- **REST** for programmatic use — a full API at `:8000` runs your agents, teams, and workflows.
- **Chat interfaces** for humans — Slack is wired in and lights up when its env vars are set; WhatsApp, Telegram, and Discord mirror the same pattern with the matching [Agno interface](https://docs.agno.com/agent-os/interfaces/overview).
- **MCP** for AI apps — an MCP server at `/mcp` lets Claude, ChatGPT, Cursor, and Claude Code drive the same agents.
- **Coding agents** work on the repo itself — five skills in [`.agents/skills/`](.agents/skills/) cover the full agent development lifecycle.

This is the Railway template of the `agentos-*` deployment family: the agent backend is identical across siblings; only the deploy layer differs — see [Portable core vs. deploy layer](#portable-core-vs-deploy-layer).

## Driven by agents

The backend ships self-driving:

- **Two flagship agents** run the platform from any frontend — the UI, Slack, or any MCP client. **Agent Builder** creates agents, teams, and workflows through AgentOS Studio tools, grounded by the Agno docs MCP and a safe registry. **Platform Manager** understands, monitors, and explains the platform: codebase wiring, eval history, deployment checks, schedules. (**WebSearch** rounds out the trio as the simplest possible agent — the sample to copy.)
- **Coding-agent skills** let Claude Code, Codex, Cursor, and other coding agents build, test, and improve the platform automatically — see [Using the platform](#using-the-platform).

Trace data, agent code, evals, and system logs are all available to coding agents, so the platform can inspect and improve itself end to end.

## Get Started

### Step 1: Run locally

> **Prerequisite:** [Docker](https://www.docker.com/get-started/) installed and running.

```sh
git clone https://github.com/agno-agi/agentos-railway.git agentos
cd agentos

# Configure credentials
cp example.env .env
# Open .env and set OPENAI_API_KEY

# Run the platform on docker
docker compose up -d --build
```

Confirm your AgentOS is running at [http://localhost:8000/docs](http://localhost:8000/docs).

### Step 2: Connect the AgentOS UI

1. Open [os.agno.com](https://os.agno.com?utm_source=github&utm_medium=example-repo&utm_campaign=agentos-railway&utm_content=agentos-railway&utm_term=railway) and sign in.
2. Click **Connect OS**, enter `http://localhost:8000` as the URL, name it **Local AgentOS**, and connect.

### Step 3: Build your first agent

1. Click **Chat** under the **Agent Builder** agent and try the first prompt: "Build an agent that tracks AI news and writes a daily brief". Go through the agent development process.
2. Once created, click the **Refresh** button on the top right. You should now see the "Daily AI News Brief" agent in the **Agents** dropdown. Click the newly created agent.
3. Ask: "What's new with Anthropic?"

### Step 4: Check platform health

Click **Chat** under **Platform Manager** and ask: "How healthy is the platform?" It answers from the codebase and runtime data — eval history, deployment checks, schedules, and the component you just built.

## Use your platform from Claude Code and chat apps

AgentOS ships an MCP server at `/mcp` (`enable_mcp_server=True` in [`app/main.py`](app/main.py)), so any MCP client can call your agents, teams, and workflows through tools like `run_agent`, `run_team`, and `run_workflow`.

**Coding agents.** One command registers the endpoint in every coding agent on the machine:

```sh
uvx agnoctl connect
```

It auto-detects Claude Code, Claude Desktop, Codex, and Cursor, registers `http://localhost:8000/mcp`, and verifies the connection with a real handshake. The manual fallback for Claude Code is `claude mcp add --transport http agentos http://localhost:8000/mcp`; any other MCP-capable tool points at the same URL. Setting up a fresh machine? Hand [`docs/setup-platform.md`](docs/setup-platform.md) to any coding agent: it takes the human from clone to connected, verifying every step.

**Chat apps.** claude.ai and ChatGPT can't reach localhost — deploy first (see below), then add your platform as a connector. In claude.ai: **Settings → Connectors → Add custom connector** → `https://<your-railway-domain>/mcp`. Same URL in ChatGPT's connector settings.

**Machine access in production.** `/mcp` sits behind the same Token-Based Authorization as the rest of the API, and it accepts two kinds of token. For a machine, mint a service-account PAT — `uvx agnoctl connect` does it for you, or `POST /service-accounts` returns an `agno_pat_…` token directly, with default scopes that cover `run_agent`, `run_team`, and `run_workflow`. JWTs minted at os.agno.com work too. Send either as `Authorization: Bearer <token>`. Locally (`RUNTIME_ENV=dev`) no token is needed.

## Portable core vs. deploy layer

This repo is the Railway sibling of the `agentos-*` deployment family. The agent backend — `agents/`, `app/`, `db/`, `workflows/`, `evals/`, the MCP server, the interfaces, and the coding-agent skills — is **portable core, identical across the family**. The **Railway-specific deploy layer** is exactly [`railway.json`](railway.json), [`scripts/railway/`](scripts/railway/), and the [Run in production](#run-in-production) section below; a sibling template (agentos-aws, agentos-fly, …) swaps only that. `Dockerfile`, `compose.yaml`, and `scripts/entrypoint.sh` are shared local-dev/runtime infra, not Railway-specific.

## Run in production

You can run the platform anywhere that supports containerized images. For the lightest lift, the codebase comes with scripts to deploy the platform to [Railway](https://railway.com).

> **Prerequisite:** [Railway CLI](https://docs.railway.com/cli#installing-the-cli) installed and `railway login` completed.

### 1. Set up your production env

Create a new `.env.production` file for production credentials.

```sh
cp .env .env.production
# Edit .env.production with production values
```

The deploy scripts read `.env.production` first and fall back to `.env`. This lets you keep separate values for local and production: different OpenAI keys, production-only credentials, a different Slack workspace. `.env.production` is gitignored.

### 2. Deploy

```sh
./scripts/railway/up.sh
```

This provisions Postgres and the app service on the same private network.

### 3. Set production auth

Token-Based Authorization is on by default. Without `JWT_VERIFICATION_KEY` or `JWT_JWKS_FILE`, the app refuses to serve traffic. The platform's job is to keep your data private, so the safe default is "refuse to start" without an authentication token.

Token-Based Auth gives you three things:

1. **No public access.** The server rejects requests without a valid token.
2. **Per-request identity.** Middleware parses the token and extracts the `user_id`, `session_id`, and custom claims. Each request is tied to a user and session, giving you auditability and traceability.
3. **Granular permissions.** User tokens can run an agent and view their own sessions. Admin tokens read everyone's sessions and test any agent.

During `./scripts/railway/up.sh`, the script creates your Railway domain and pauses so you can mint the key before the app starts.

1. Open [os.agno.com](https://os.agno.com?utm_source=github&utm_medium=example-repo&utm_campaign=agentos-railway&utm_content=agentos-railway&utm_term=railway), click **Connect OS** → **Live**, enter your Railway domain, and connect.
2. Name it **Live AgentOS**.
3. Go to **Settings** → **OS & Security**.
4. Turn **Token-Based Authorization (JWT)** on.
5. Copy the public key.
6. Paste the full public key into the `up.sh` prompt. The script saves it into your env file for future syncs:

```sh
JWT_VERIFICATION_KEY=-----BEGIN PUBLIC KEY-----
MIIBIjANBgkq...
-----END PUBLIC KEY-----
```

> **Heads up.** Live AgentOS Connections are a paid feature. Use `PLATFORM30` to get 1 month off. We are working on a free trial so you don't have to pay to try.

If you run non-interactively or skip the prompt, you can sync environment variables later with `./scripts/railway/env-sync.sh`.

### 4. Verify

You can check the logs on the Railway dashboard, or by running the following command:

```sh
railway logs --service agent-os
```

### 5. Redeploy after code changes

For one-off updates from your machine, run the following command:

```sh
./scripts/railway/redeploy.sh
```

To auto-deploy on every push to `main`, follow these steps:

1. Open the Railway dashboard, your project, the agent-os service, **Settings**.
2. Under **Source**, click **Connect Repo** and pick your repo.
3. Set the deploy branch to `main` and save.

Push to `main` triggers a build and rolling deploy. `./scripts/railway/env-sync.sh` is still how you sync env changes.

### 6. Sync environment variables

To re-sync environment variables, run the following command:

```sh
./scripts/railway/env-sync.sh
```

### Opting out of JWT (not recommended)

Set `authorization=False` in [`app/main.py`](app/main.py) and redeploy. Use this only inside a private VPC behind another auth layer. Without it, anyone who guesses your Railway domain can access your platform.

## Using the platform

This platform is designed around the **create → improve → evaluate → maintain** workflow.

Create agents/teams/workflows from the UI or using a coding agent, improve and evaluate with the skills, and maintain with a recurring drift sweep.

### Create

**From the UI, chat, or a coding agent.** Open **Agent Builder** and describe the job, as in the quickstart — or reach it through Slack or any MCP frontend. Agent Builder pulls framework details from the Agno docs MCP, picks tools and models from the safe Studio registry, and creates components immediately: creating publishes version 1, later edits stay drafts until published, and only deletes pause for your approval. It runs the component only when you ask.

**From a coding agent.** For agents that live in the repo, open your coding agent of choice (Claude Code, Codex, Cursor) and run:

```
/create-new-agent
```

It asks a few questions, generates the agent file in `agents/`, registers it in `app/main.py`, adds quick prompts to `app/config.yaml`, restarts the container, and smoke-tests it live.

### Improve

Chat with your agent at [os.agno.com](https://os.agno.com?utm_source=github&utm_medium=example-repo&utm_campaign=agentos-railway&utm_content=agentos-railway&utm_term=railway). Run realistic prompts, try edge cases, watch the traces and sessions.

Then improve your agents by running the following skills:

- **`/extend-agent`** — Add a tool, add a capability, refine the instructions, fix a known bug.
- **`/improve-agent`** — Claude simulates scenarios from the agent's `INSTRUCTIONS`, runs them against the live container, judges the responses, and edits until they pass.

### Evaluate

Run the eval suite to check for regressions. The eval cases live in [`evals/cases.py`](evals/cases.py), tagged with profiles. The evals run on the host machine, so set up the venv with `./scripts/venv_setup.sh && source .venv/bin/activate`, then:

```sh
python -m evals --profile smoke     # fast checks of the self-driving surfaces
```

If a case fails, run **`/eval-and-improve`** — it diagnoses each failure, fixes what's in scope, and loops until green.

### Maintain

Because the repo is managed primarily by coding agents, it moves fast. Run `/review-and-improve` before a release or after a refactor: it sweeps for drift between docs, code, and config, auto-fixes mechanical drift like stale paths and missing env vars, and flags anything bigger.

From chat, ask **Platform Manager** for a health check any time — it reads the latest deployment-check report and eval history, diagnoses, and names the skill or action to fix what it finds.

## Environment variables

`compose.yaml` sets the dev defaults (`RUNTIME_ENV=dev`, `AGNO_DEBUG=True`, `WAIT_FOR_DB=True`) so local Docker skips JWT and waits for Postgres before serving.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | yes | none | OpenAI key for models and embeddings. |
| `RUNTIME_ENV` | no | `prd` | `dev` disables JWT. Compose sets this to `dev` for local — never put it in an env file that syncs to Railway, or production deploys unauthenticated. |
| `JWT_VERIFICATION_KEY` | prd | none | Public key from os.agno.com. Required when `RUNTIME_ENV=prd`, unless `JWT_JWKS_FILE` is set. |
| `JWT_JWKS_FILE` | prd | none | Path to a JWKS file; alternative to `JWT_VERIFICATION_KEY` for production JWT verification. |
| `AGENTOS_URL` | no | `http://127.0.0.1:8000` | Scheduler base URL. `scripts/railway/up.sh` auto-sets it to your Railway domain; set by hand only for a custom domain or tunnel. |
| `ENABLE_DEPLOY_CHECK` | no | `True` | The reference deployment-check cron runs daily by default. Set `False` to disable; the workflow is runnable on demand regardless. |
| `ENABLE_SCHEDULED_EVALS` | no | `False` | If `True`, schedules the run-evals workflow daily. Off by default because it uses model calls. |
| `EVALS_PROFILE` | no | `smoke` | Eval profile used by the run-evals workflow. |
| `EVALS_CASE_TIMEOUT_SECONDS` | no | `90` | Default per-case timeout for run-evals runs; applies only to cases that don't set their own `timeout_seconds`. |
| `EVALS_SUITE_TIMEOUT_SECONDS` | no | `900` | Whole-suite timeout for run-evals runs; per-case timeouts are the granular limit. The default bounds the `smoke` profile's worst case (incl. builder-case teardown). |
| `PARALLEL_API_KEY` | no | none | Authenticates the WebSearch Agent's Parallel SDK / MCP connection. |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` | no | none | Both must be set to enable the Slack interface. |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASS` / `DB_DATABASE` | no | matches compose | Postgres connection. |
| `DB_DRIVER` | no | `postgresql+psycopg` | SQLAlchemy driver. |
| `AGNO_DEBUG` | no | `False` | If `True`, Agno emits verbose debug logs. Compose sets this for dev. |
| `WAIT_FOR_DB` | no | `False` | If `True`, the entrypoint blocks on the DB before starting. Compose sets this. |

## Learn more

- [Agno documentation](https://docs.agno.com?utm_source=github&utm_medium=example-repo&utm_campaign=agentos-railway&utm_content=agentos-railway&utm_term=railway)
- [AgentOS introduction](https://docs.agno.com/agent-os/introduction?utm_source=github&utm_medium=example-repo&utm_campaign=agentos-railway&utm_content=agentos-railway&utm_term=railway)
- [Agno on GitHub](https://github.com/agno-agi/agno). Drop a star if this is useful.
