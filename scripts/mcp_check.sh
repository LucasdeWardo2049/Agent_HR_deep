#!/bin/bash

############################################################################
#
#    MCP Smoke Check
#
#    Calls the AgentOS MCP endpoint end to end: handshake, tool count,
#    then one run_agent call. Runs the client inside the container.
#
#    Usage:
#      ./scripts/mcp_check.sh                          # default question
#      ./scripts/mcp_check.sh "What does web-search do?"
#
############################################################################

set -e

QUESTION="${1:-Which agents are registered in this AgentOS?}"

docker compose exec -T agentos-api python - "$QUESTION" <<'PY'
import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    async with streamablehttp_client("http://localhost:8000/mcp", timeout=180) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"MCP OK — {len(tools.tools)} tools")
            result = await session.call_tool(
                "run_agent",
                {"agent_id": "platform-manager", "message": sys.argv[1]},
                read_timeout_seconds=None,
            )
            # run_agent returns a trimmed ToolResult: content[0].text is the plain
            # answer, and structuredContent carries {run_id, session_id, status}.
            print("AGENT RESPONSE:\n", result.content[0].text)


asyncio.run(main())
PY
