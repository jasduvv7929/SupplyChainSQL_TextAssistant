"""
Stage 1 test: confirm langchain-mcp-adapters can connect to our FastMCP
server and load its tools as LangChain-compatible tools, with no LLM
involved yet. This isolates "does the MCP bridge work" from "does the
agent reasoning work" -- two separate things that should be debugged
separately if something breaks.
"""

import asyncio
import os
from langchain_mcp_adapters.client import MultiServerMCPClient

# Resolve the path to server.py relative to this file, same portability
# principle as server.py's own DB_PATH resolution.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_PATH = os.path.join(_THIS_DIR, "..", "mcp_server", "server.py")
SERVER_PATH = os.path.abspath(SERVER_PATH)


async def main():
    client = MultiServerMCPClient(
        {
            "supply_chain": {
                "command": "python",
                "args": [SERVER_PATH],
                "transport": "stdio",
            }
        }
    )

    tools = await client.get_tools()

    print(f"Loaded {len(tools)} tools from the MCP server:\n")
    for tool in tools:
        print(f"  - {tool.name}: {tool.description[:80]}...")

    # Actually call one tool end-to-end, to confirm this isn't just
    # listing metadata but genuinely invoking the server.
    print("\n--- Calling list_tables via the LangGraph-wrapped tool ---")
    list_tables_tool = next(t for t in tools if t.name == "list_tables")
    result = await list_tables_tool.ainvoke({})
    print(result)


if __name__ == "__main__":
    asyncio.run(main())