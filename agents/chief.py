"""
Chief Agent
===========

The platform's second brain. Chief holds what your team is building and
thinking: durable notes in its own filesystem, an entity graph over the people
and projects around you, and what it learns about how each user works.

The stores split the work:
- Notes (FileSystem) hold the content: decisions with their reasoning, running
  documents, anything longer than a line.
- Entities index the world: people, projects, systems — one-line current
  values, links, and a note pointer to where the detail lives.
- Profile and memory hold the self: who each user is and how they like to work.

The world is shared, the self is private: notes and entities are one brain for
everyone on this platform; profile and memory stay per-user.
"""

from agno.agent import Agent
from agno.fs import FileSystem
from agno.learn import (
    EntityMemoryConfig,
    LearningMachine,
    LearningMode,
    UserMemoryConfig,
    UserProfileConfig,
)

from app.settings import default_model
from db import get_postgres_db

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
You are Chief — this platform's second brain. You hold what your team is
building and thinking, and you answer from what you hold.

One claim, one home. Notes hold the content; entities are the index over it:
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

Reading is the other half: for any "why", "what did we decide", "where does X
stand" — follow the entity's note: pointer, read the note, and answer from it,
not from the injected one-liners.

When asked whether something has come up before and you find nothing, say what
you searched (the entity directory and your notes) — a grounded no.

Answer in under 3 sentences unless asked for more.\
"""

chief = Agent(
    id="chief",
    name="Chief",
    model=default_model(),
    db=get_postgres_db(),
    # The learning machine attaches its tools, guidance, and recall automatically.
    learning=brain,
    tools=[notes.tools()],
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
