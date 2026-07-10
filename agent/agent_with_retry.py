"""
Stage 3: wraps the Stage 2 agent with a bounded self-correction loop.

Two distinct failure modes observed in Stage 2 testing, handled differently:

1. Malformed tool-call generation (Groq BadRequestError, 'tool_use_failed').
   Observed rate: ~1/8 runs on the hardest test question. This happens at
   the LLM-call level -- the model emits text that looks like a tool call
   but isn't valid JSON/structured format. We catch this and retry the
   whole agent invocation, since the malformed generation isn't visible
   to the agent itself as a recoverable error (it crashes the call before
   a ToolMessage can even be created).

2. SQL execution errors and bad query plans (run_query/run_explain errors).
   These are already visible to the agent as ToolMessage content, and we
   observed in Stage 2 that the model frequently self-corrects on these
   already (e.g. recovering from "no such table: dim_products" by calling
   describe_table and retrying). We don't need to intercept these
   ourselves -- we just need a CEILING on how many total tool-call steps
   are allowed, since an unbounded loop could in principle retry forever
   on a confused model. We observed up to 10 tool calls on a real,
   eventually-successful run, so the ceiling needs to allow real recovery
   while still being bounded.

MAX_API_RETRIES = 3: chosen from the observed ~1/8 (~12%) single-attempt
failure rate on malformed tool calls. 3 retries reduces the chance of a
full failure to roughly (0.12)^3 ~ 0.2%, assuming failures are independent
between attempts (a simplifying assumption -- we have not verified
independence, but it is a reasonable starting assumption for this error
class, which look like one-off generation glitches rather than a
sustained model state issue).

MAX_AGENT_STEPS = 15: set above the highest step count observed in
real, successful runs (10), to allow genuine multi-step recovery, while
still being a hard ceiling rather than no ceiling at all.
"""

import asyncio
import os
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_groq import ChatGroq
from langchain.agents import create_agent
from groq import BadRequestError, RateLimitError
from langgraph.errors import GraphRecursionError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from groq import RateLimitError
import re

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
SERVER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_server", "server.py")
)

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

MAX_API_RETRIES = 3
MAX_AGENT_STEPS = 15


async def ask_with_self_correction(question: str, verbose: bool = True) -> dict:
    """
    Run the agent against a question, with a bounded retry loop around
    the known malformed-tool-call failure mode, and a hard ceiling on
    total agent steps (LangGraph's own recursion_limit, which counts
    each model-call + tool-call round as steps).

    Returns a dict: {"success": bool, "answer": str | None,
                      "attempts_used": int, "error": str | None}
    """
    # Guardrail: check scope before committing to the full agent loop.
    from guardrail import is_in_scope
    scope_check = await is_in_scope(question)
    if not scope_check["in_scope"]:
        if verbose:
            print(f"[guardrail] Question rejected as out-of-scope: {scope_check['raw_response']}")
        return {
            "success": False,
            "answer": None,
            "attempts_used": 0,
            "error": "This question doesn't appear to be answerable using the supply chain database "
                     "(covers suppliers, warehouses, products, inventory, orders, and shipments).",
            "full_trace": None,
        }
    mcp_server_url = os.environ.get("MCP_SERVER_URL")
    if mcp_server_url:
        if not mcp_server_url.endswith("/mcp"):
            mcp_server_url = mcp_server_url.rstrip("/") + "/mcp"
        client = MultiServerMCPClient(
            {"supply_chain": {"url": mcp_server_url, "transport": "streamable_http"}}
        )
    else:
           # Local development: spawn the MCP server as a subprocess via stdio
        client = MultiServerMCPClient(
            {"supply_chain": {"command": "python", "args": [SERVER_PATH], "transport": "stdio"}}
        )
    tools = await client.get_tools()
    llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
    agent = create_agent(llm, tools, system_prompt=SYSTEM_PROMPT)

    last_error = None
    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": question}]},
                # LangGraph's own ceiling on total graph steps (model
                # calls + tool calls combined). Distinct from
                # MAX_API_RETRIES, which governs retrying a single
                # failed *invocation*, not steps within one invocation.
                config={"recursion_limit": MAX_AGENT_STEPS},
            )
            answer = result["messages"][-1].content
            if verbose:
                print(f"[attempt {attempt}/{MAX_API_RETRIES}] Success.")
            return {
                "success": True,
                "answer": answer,
                "attempts_used": attempt,
                "error": None,
                "full_trace": result["messages"],
            }
        except BadRequestError as e:
            last_error = f"Malformed tool-call generation: {str(e)[:150]}"
            if verbose:
                print(f"[attempt {attempt}/{MAX_API_RETRIES}] Malformed tool call, retrying...")
            continue
        except RateLimitError as e:
            wait_time = _extract_retry_delay(str(e))
            if wait_time > 120:
                # If Groq is asking us to wait more than 2 minutes,
                # this is very likely the daily token cap, not a
                # per-minute one -- waiting it out inline isn't
                # practical, so surface immediately rather than
                # blocking for a long, uncertain amount of time.
                return {
                    "success": False,
                    "answer": None,
                    "attempts_used": attempt,
                    "error": f"Rate limit hit, wait time too long to retry inline ({wait_time:.0f}s): {str(e)[:150]}",
                    "full_trace": None,
                }
            if verbose:
                print(f"[attempt {attempt}/{MAX_API_RETRIES}] Rate limited, waiting {wait_time:.1f}s (Groq-specified)...")
            await asyncio.sleep(wait_time)
            last_error = f"Rate limit (waited {wait_time:.1f}s): {str(e)[:150]}"
            continue
        except GraphRecursionError as e:
            # Hit our MAX_AGENT_STEPS ceiling -- the model couldn't
            # converge on an answer within the step budget. Retrying
            # the same question from scratch is unlikely to help if
            # the question is genuinely too hard for this model, so
            # we surface this distinctly rather than burning retries.
            return {
                "success": False,
                "answer": None,
                "attempts_used": attempt,
                "error": f"Hit step ceiling ({MAX_AGENT_STEPS} steps) without converging: {str(e)[:150]}",
                "full_trace": None,
            }
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:150]}"
            if verbose:
                print(f"[attempt {attempt}/{MAX_API_RETRIES}] {last_error}")
            continue

    return {
        "success": False,
        "answer": None,
        "attempts_used": MAX_API_RETRIES,
        "error": f"Exhausted {MAX_API_RETRIES} retries. Last error: {last_error}",
        "full_trace": None,
    }

def _extract_retry_delay(error_message: str, default: float = 30.0) -> float:
    """
    Groq's 429 error messages include a specific wait time, e.g.
    "Please try again in 5m47.328s." or "Please retry in 26.27s."
    Extract it so we wait exactly as long as Groq actually asks,
    rather than guessing a fixed delay. Falls back to `default`
    seconds if the message doesn't match the expected pattern (the
    format isn't a documented, guaranteed contract -- this is
    pattern-matching observed behavior, not a stable API field).
    """
    match = re.search(r"(?:try again|retry) in (?:(\d+)m)?([\d.]+)s", error_message)
    if not match:
        return default
    minutes = float(match.group(1)) if match.group(1) else 0.0
    seconds = float(match.group(2))
    return minutes * 60 + seconds

async def main():
    question = "Which warehouses have products below reorder point, and what region are they in?"
    result = await ask_with_self_correction(question)
    print("\n--- Result ---")
    print(f"Success: {result['success']}")
    print(f"Attempts used: {result['attempts_used']}")
    if result["success"]:
        print(f"Answer:\n{result['answer']}")
    else:
        print(f"Error: {result['error']}")


if __name__ == "__main__":
    asyncio.run(main())