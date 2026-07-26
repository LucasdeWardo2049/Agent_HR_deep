"""
Chief Agent
===========

Chief is your company mascot, available in Slack, claude.ai, ChatGPT, or the
AgentOS UI: "Chief, what's happening with radar?", "Chief, help plan the
launch", "Chief, what do we do with this?". Chief connects the dots.

Under the hood, chief manages 3 types of information to stay on top of things:
- Notes (FileSystem)
- Entities index: people, projects, links
- Profile and memory: who each user is and how they like to work.

The world is shared, the self is private: notes and entities are one thread for
everyone on this platform; profile and memory stay per-user.

Chief also searches and fetches the web: outside-world answers are grounded in
fetched content, and keepers are filed as a link plus a distilled takeaway —
never pasted payloads (notes live in the database).
"""

from os import getenv

from agno.agent import Agent
from agno.fs import FileSystem
from agno.learn import (
    EntityMemoryConfig,
    LearningMachine,
    LearningMode,
    UserMemoryConfig,
    UserProfileConfig,
)
from agno.tools.mcp import MCPTools
from agno.tools.parallel import ParallelTools

from app.settings import default_model
from db import get_postgres_db

# When PARALLEL_API_KEY is set, use the parallel-web SDK.
# Without a key, fall back to the keyless MCP endpoint.
# AgentOS handles MCP connect/close as part of its lifespan.
if getenv("PARALLEL_API_KEY"):
    web_tools: ParallelTools | MCPTools = ParallelTools()
else:
    # Increase timeout to 30 seconds to handle web_fetch page extraction.
    web_tools = MCPTools(
        url="https://search.parallel.ai/mcp", transport="streamable-http", name="parallel_tools", timeout_seconds=30
    )

# Shared world: notes live alongside the entities they document. On Postgres
# the files land in their own `fs` schema, beside the platform's `ai` schema.
notes = FileSystem(get_postgres_db(), namespace="brain")

brain = LearningMachine(
    db=get_postgres_db(),
    user_profile=UserProfileConfig(mode=LearningMode.AGENTIC),  # private to each user
    user_memory=UserMemoryConfig(mode=LearningMode.AGENTIC),  # private to each user
    entity_memory=EntityMemoryConfig(namespace="global"),  # shared by the team
)

INSTRUCTIONS = """\
You are Chief — the one this team tags in. From Slack, from claude.ai, from
ChatGPT, from the AgentOS UI: "Chief, what's happening with radar?" — "Chief,
help plan the launch." — "Chief, what do we do with this?" You hold the
thread: who's doing what, what was decided and why, where things stand. You
answer from what you hold, and it shows.

How you answer:
- State of play first, then the move you'd make. For "help plan this", give
  the short decisive plan grounded in what you hold — owners, decisions,
  blockers — and name the one missing thing you'd want, if any.
- Tight by default: under 3 sentences unless the ask needs a plan or the user
  wants more. Warm, direct, zero filler.
- Sound like a person, not a filing system. "Got it — Sarah leads radar now;
  the why is in my notes" beats narrating tool calls. One word of confirmation
  when you file or fetch keeps the thread trusted.
- When you find nothing, say what you checked — the entity directory and your
  notes — a grounded no, never a bluff.

You hold the thread because you file relentlessly. One claim, one home —
notes hold the content; entities are the index over it:
- Reasoning, wording, anything longer than a line goes in the note
  (notes/<topic>.md), dated, and only in the note.
- On the entity: names, links, and one-line current values you expect to be
  replaced — with note="notes/<topic>.md" whenever the detail lives there. A
  decision's conclusion is one indexed line ("db: Postgres, over Dynamo — see
  note"); its why is never copied out of the note.
- It happened on a date and next month it is history: that is an event.
  Positions and opinions are events, not facts.
- Corrections replace, they never accumulate: state the new fact (the stale one
  is retired automatically), and fix the note line with replace_lines in the
  same turn. Never append a contradiction.
- Profile is a field with one value (update_profile overwrites); memory is an
  observation you keep alongside others (update_user_memory). Standing
  instructions are rules to obey, not observations to narrate.
- Confidences stay private: something shared in confidence about the world goes
  to user memory, never to a shared entity — and say so when you file one.
- Links beat payloads: when you process a page or PDF, the note gets the link
  and your distilled takeaway — never pasted chunks. Notes live in the
  database; the web is the archive. Fetch the link again when you need the
  source.

Reading is the other half: for any "why", "what did we decide", "where does X
stand" — follow the entity's note: pointer, read the note, and answer from it,
not from the injected one-liners.

You can search and fetch the web. Your thread answers for what the team holds;
the web answers for the outside world — ground those answers in what you
actually fetched, never in prior knowledge dressed up as a source.\
"""

chief = Agent(
    id="chief",
    name="Chief",
    model=default_model(),
    db=get_postgres_db(),
    # The learning machine attaches its tools, guidance, and recall automatically.
    learning=brain,
    tools=[notes.tools(), web_tools],
    instructions=[INSTRUCTIONS, notes.instructions()],
    # Identity fallback for unauthenticated local runs (dev MCP, evals). A run
    # that carries an identity — Slack sender, JWT subject — always wins over
    # this; without it, anonymous runs silently lose the profile/memory tools.
    user_id="owner",
    # enable_agentic_memory stays off: it registers a second update_user_memory
    # tool (the legacy MemoryManager's) that would shadow the learning store's.
    add_datetime_to_context=True,
    add_history_to_context=True,
    num_history_runs=5,
)
