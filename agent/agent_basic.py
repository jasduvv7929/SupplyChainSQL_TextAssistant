"""
Stage 2: a basic LangGraph ReAct agent wired to a Groq-hosted Llama
model, using the MCP server's tools. No self-correction loop yet
(that's Stage 3) -- this stage just proves the agent can take a
natural language question, decide to call MCP tools, and produce an
answer.

"""

import asyncio, os
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_groq import ChatGroq
from langchain.agents import create_agent

load_dotenv("../.env")
SERVER_PATH = os.path.abspath("../mcp_server/server.py")

SYSTEM_PROMPT = (
    "You are a SQL assistant for a supply chain database. "
    "Several tables/views in this schema are grained more finely than "
    "the entity a question may be asking about -- for example, "
    "fct_inventory_risk has one row per product PER WAREHOUSE, so a "
    "product at risk in multiple warehouses appears as multiple rows. "
    "Before writing a query, check whether the question asks about a "
    "distinct entity (e.g. 'how many products', 'which suppliers') "
    "versus a finer-grained combination (e.g. 'which warehouse-product "
    "pairs'). If the question asks about the coarser entity, use "
    "DISTINCT or GROUP BY on that entity's key to avoid duplicate rows "
    "from the underlying join grain."
)

async def run_once(i):
    client = MultiServerMCPClient({"supply_chain": {"command": "python", "args": [SERVER_PATH], "transport": "stdio"}})
    tools = await client.get_tools()
    llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
    agent = create_agent(llm, tools, system_prompt=SYSTEM_PROMPT)
    question = "Which products are at risk of stockout, and who is the primary supplier for each?"
    result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]})

    # Find the actual run_query call(s) and check for DISTINCT/GROUP BY
    queries = []
    for msg in result["messages"]:
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                if tc["name"] == "run_query":
                    queries.append(tc["args"].get("sql", ""))
    last_query = queries[-1] if queries else "NO QUERY FOUND"
    has_dedup = "DISTINCT" in last_query.upper() or "GROUP BY" in last_query.upper()
    print(f"Run {i}: has_distinct_or_groupby={has_dedup}")
    print(f"  Query: {last_query}")

async def main():
    for i in range(3):
        await run_once(i)

asyncio.run(main())