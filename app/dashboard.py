"""
Streamlit dashboard for the supply chain Text-to-SQL agent.

Design constraints, given free-tier token limits observed during
development:
- Exactly ONE agent call per question submission, no auto-refresh,
  no background polling.
- The async agent function (ask_with_self_correction) is run via
  asyncio.run() inside the button callback, since Streamlit's
  execution model is otherwise synchronous.
- Shows three things per answer: the final natural-language answer,
  the generated SQL (transparency into what actually ran), and the
  full tool-call trace (demonstrates the self-correction loop, the
  project's main differentiator, when it triggers).
"""

import asyncio
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
from agent_with_retry import ask_with_self_correction

st.set_page_config(page_title="Supply Chain SQL Assistant", page_icon="📦", layout="wide")

st.title("📦 Supply Chain Text-to-SQL Assistant")
st.caption(
    "Agentic text-to-SQL over a supply chain database, via MCP + LangGraph. "
    "Ask a question in plain English; the agent decides which tools to call, "
    "writes SQL, and self-corrects on errors."
)

with st.expander("ℹ️ About this system", expanded=False):
    st.markdown(
        """
        **Architecture:** Custom FastMCP server (4 tools: `list_tables`,
        `describe_table`, `run_query`, `run_explain`) exposing both raw
        OLTP tables and dbt-built mart views, queried by a LangGraph
        agent (`llama-3.3-70b-versatile` via Groq) with a bounded
        self-correction loop.

        **Security:** three-layer defense on the MCP server --
        SQLite connection-level read-only mode, OS-level file
        permissions, and AST-based query validation (rejects anything
        that isn't a single SELECT/WITH statement, including stacked
        queries).

        **Known limitations:** smaller/free-tier models occasionally
        produce malformed tool calls or pick an unrelated table on the
        hardest multi-join questions -- the retry loop catches the
        former; the eval set (`eval/`) measures the actual rate of
        the latter rather than assuming it away.
        """
    )

question = st.text_input(
    "Ask a question about the supply chain data:",
    placeholder="e.g. Which suppliers have the highest late delivery percentage?",
)

col1, col2 = st.columns([1, 5])
with col1:
    submit = st.button("Ask", type="primary")

if submit and question.strip():
    with st.spinner("Agent is thinking... (this may take 10-30+ seconds for multi-step questions)"):
        try:
            result = asyncio.run(ask_with_self_correction(question, verbose=False))
        except Exception as e:
            st.error(f"Unexpected error: {e}")
            result = None

    if result is not None:
        if result["success"]:
            st.success("Answer")
            st.markdown(result["answer"])

            # Pull out the SQL and tool-call trace for transparency.
            sql_calls = []
            for msg in result.get("full_trace", []) or []:
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        sql_calls.append((tc["name"], tc["args"]))

            with st.expander(f"🔧 Tool calls made ({len(sql_calls)})", expanded=False):
                if not sql_calls:
                    st.write("No tool calls were made.")
                for name, args in sql_calls:
                    if name == "run_query" and "sql" in args:
                        st.code(args["sql"], language="sql")
                    else:
                        st.write(f"`{name}({args})`")

            st.caption(f"Resolved in {result['attempts_used']} attempt(s).")
        else:
            st.error(f"The agent could not answer this question: {result['error']}")
elif submit:
    st.warning("Please enter a question.")

st.divider()
st.caption(
    "Built as a portfolio project demonstrating agentic text-to-SQL via MCP. "
    "See the eval/ folder for measured accuracy across 26 verified test questions."
)