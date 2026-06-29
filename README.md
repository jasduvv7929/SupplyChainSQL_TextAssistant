# Agentic Text-to-SQL Assistant for Supply Chain Analytics

An agentic text-to-SQL system that answers natural-language questions about a supply chain database. Built around the Model Context Protocol (MCP), a LangGraph agent, and a bounded self-correction loop, with a dbt semantic layer underneath and a three-layer security model protecting the database from the LLM's own output.

## What this is

A user asks a question in plain English ("which suppliers have the worst on-time delivery rate?"). A LangGraph agent, backed by a Groq-hosted Llama 3.3 70B model, decides which tools it needs, inspects the database schema, writes SQL, executes it through a custom MCP server, and self-corrects if the query fails or returns something unexpected. The MCP server exposes both raw operational tables and a small set of dbt-built semantic marts, and enforces read-only access at three independent layers regardless of what SQL the model generates.

The project exists to demonstrate the engineering pattern behind production "ask your data" tools (Snowflake Cortex Analyst, Microsoft Fabric Data Agents) using an open architecture: a protocol-based tool layer (MCP) instead of a vendor-specific integration, an explicit agent loop instead of a single-shot prompt, and a measured evaluation set instead of a handful of cherry-picked demo queries.

## Architecture

```
                     ┌─────────────────────┐
   Natural language  │   LangGraph Agent    │
   question  ───────▶│  (Llama 3.3 70B,     │
                     │   via Groq)          │
                     └──────────┬───────────┘
                                │  MCP protocol (stdio)
                                ▼
                     ┌─────────────────────┐
                     │   FastMCP Server     │
                     │  list_tables         │
                     │  describe_table      │
                     │  run_query           │
                     │  run_explain         │
                     └──────────┬───────────┘
                                │  3-layer read-only enforcement
                                ▼
        ┌───────────────────────────────────────────┐
        │              SQLite Database               │
        │  raw tables (suppliers, orders, shipments…) │
        │  + dbt marts (fct_supplier_performance,     │
        │               fct_inventory_risk)           │
        └───────────────────────────────────────────┘
```

A guardrail classification step sits in front of the main agent loop: a small, tool-free LLM call checks whether an incoming question is answerable from this database before the multi-step agent runs at all, so out-of-scope questions fail fast and cheaply rather than triggering a full agent loop with no correct path forward.

## What this demonstrates

- **Protocol-based tool architecture.** A custom MCP server, not a prebuilt connector, exposing schema introspection and query execution as standardized tools any MCP-compatible client can use.
- **A genuine agentic loop, not a single-shot wrapper.** The agent inspects schema, writes SQL, observes execution errors or unexpected results, and retries, bounded by an explicit retry ceiling and a LangGraph recursion limit, not an unbounded loop. Query-plan checking is deterministic, not left to the model's judgment: `run_explain` parses SQLite's `EXPLAIN QUERY PLAN` output in Python and flags any `SCAN` (full table scan) in the plan as a boolean `contains_full_scan` field, which the agent can act on, rather than passing the raw plan text to the LLM and trusting it to notice.
- **A dbt semantic layer.** Two mart models (`fct_supplier_performance`, `fct_inventory_risk`) pre-compute business logic that would otherwise need to be re-derived correctly by the LLM on every single query, the same problem a real analytics team solves by centralizing metric definitions instead of letting them drift across reports.
- **Defense-in-depth security**, not a single validation step. Connection-level read-only enforcement, OS-level file permissions, and AST-based SQL parsing (rejecting multi-statement queries, not just non-`SELECT` ones) are independent layers, each a backstop if the layer above is somehow bypassed.
- **A measured evaluation set built on Execution Accuracy**, the same methodology used by the Spider and BIRD text-to-SQL benchmarks: the agent's generated SQL is re-executed and its actual result compared against ground truth, not its prose summary.

## Tech stack and why

| Component | Choice | Why |
|---|---|---|
| Database | SQLite | Zero hosting cost, zero setup for anyone cloning the repo, instantly reproducible via a seeded generator script. Free-tier cloud warehouses (Snowflake, Supabase, Neon) all carry either trial expirations or idle-pause behavior unsuitable for a portfolio artifact meant to run indefinitely. |
| Semantic layer | dbt Core (`dbt-sqlite` adapter) | Centralizes business logic (what counts as a late delivery, what counts as stockout risk) in one tested place rather than letting the LLM re-derive it, inconsistently, on every query. |
| Protocol layer | Custom FastMCP server | A hand-built server, not a vendor-managed one, to demonstrate actual protocol-level understanding: tool design, schema introspection, and security enforcement at the server boundary. |
| Agent framework | LangGraph (`create_agent`) | Explicit, debuggable agent state and step-by-step tool orchestration, the current (non-deprecated) entry point in the LangChain/LangGraph ecosystem. |
| LLM | Llama 3.3 70B via Groq | Chosen after direct empirical comparison: a smaller 17B model failed every multi-step, schema-dependent question tested (joins across raw tables and dbt marts), while the 70B model succeeded consistently and self-corrected from incorrect table and column assumptions mid-conversation. Selected over Gemini's free tier, which currently caps newly-released models at a request volume too low for iterative agent development. |
| Frontend | Streamlit | A lightweight, transparent interface showing not just the final answer but the generated SQL and the full tool-call trace, so the agent's reasoning is visible, not a black box. |

## Security model

The MCP server enforces read-only access at three layers, each catching a different failure shape rather than duplicating the same check:

1. **Connection-level.** The database connection is opened using SQLite's URI `mode=ro` flag. The SQLite engine itself physically rejects any `INSERT`, `UPDATE`, or `DELETE`, regardless of what the application code does. This is the layer that would catch a single, non-stacked write statement (e.g. `DELETE FROM suppliers`) that Python's `sqlite3` driver would otherwise pass through to the database without complaint.
2. **Filesystem-level.** The database file itself is set read-only at the operating-system level, so even an unrelated process or a bug elsewhere on the same machine cannot write to it, independent of how that process connects to the database.
3. **Application-level.** Every incoming query is parsed into an abstract syntax tree before ever reaching the database. Only a single statement rooted at `SELECT` or `WITH` is permitted. Python's `sqlite3` driver already rejects multi-statement strings passed to `.execute()` (raising `ProgrammingError: You can only execute one statement at a time`), so this layer's added value is rejecting single, non-stacked write statements at the earliest possible point, before a connection is even opened, and giving the agent a clear, structured error message to self-correct on, rather than a low-level driver exception.

## Known limitations

Documented honestly rather than hidden, since a known, measured limitation is more credible than an unblemished claim:

- **Smaller and mid-size open-weight models occasionally produce malformed tool calls.** Direct testing measured this at roughly a 1-in-8 rate on Groq's free tier for the hardest question in the eval set. The retry wrapper catches and retries this specific failure mode.
- **The model can default to a less direct table when multiple tables could answer a question**, even when the answer ends up correct (e.g., querying a pre-aggregated mart for a question that the raw table would answer just as well). This doesn't affect correctness but is worth noting as a reasoning-path quirk.
- **A one-to-many join grain mismatch was found and partially mitigated, not fully solved.** `fct_inventory_risk` is grained at product-per-warehouse; a question about distinct products can return duplicate rows without explicit `DISTINCT`/`GROUP BY`. A system prompt instruction reduces this failure rate but was measured at roughly two-thirds effective across repeated trials, not a guarantee. This exact failure mode is deliberately encoded as test cases in the eval set (Category 4) rather than assumed away.
- **Free-tier rate limits are a real constraint, mitigated at two levels.** Groq enforces limits on requests-per-minute, tokens-per-minute, and tokens-per-day, and a single multi-step agent question (schema introspection, query execution, possible retries) can consume several thousand tokens across multiple LLM calls, easily exhausting a tighter per-minute cap well before the daily cap is reached. The agent itself parses Groq's actual rate-limit error message to extract the real wait time it specifies (handling the two different message formats observed in practice) and retries automatically for short, per-minute-style waits; if Groq reports a long wait (over two minutes, indicating the daily cap rather than a transient per-minute limit), the agent fails fast rather than blocking indefinitely. The eval scoring script adds a smaller proactive delay between questions on top of this, to reduce how often the limit is hit in the first place, and saves results incrementally so a rate-limit failure mid-run never discards already-completed results.

## Evaluation

26 hand-written questions across five difficulty categories, each with a ground-truth answer computed and verified directly against the database, scored using Execution Accuracy: the agent's generated SQL is re-executed independently and its actual result compared against the expected answer, not its prose summary.

| Category | Description | Result |
|---|---|---|
| 1 — Easy | Single table/view, no joins | 8/8 |
| 2 — Medium | Single join | partial, in progress |
| 3 — Hard | Multi-join, raw-table-vs-dbt-mart ambiguity | not yet run |
| 4 — Trap | Deliberate grain/duplication mismatch | not yet run |
| 5 — Trend | Aggregation, date logic, seasonal patterns | not yet run |

Full results across all 26 questions, broken down by category, will be finalized once evaluation is complete.

## Project structure

```
sql-mcp-agent/
├── build.py             one-command setup: generate data, build dbt marts, lock the DB
├── db/                  schema, synthetic data generator, manage_lock.py, supply_chain.db
├── supply_chain_dbt/    dbt project: fct_supplier_performance, fct_inventory_risk
├── mcp_server/          FastMCP server: 4 tools, 3-layer security
├── agent/               LangGraph agent, self-correction wrapper, guardrail
├── eval/                26-question eval set and Execution Accuracy scorer
└── app/                 Streamlit dashboard
```

## Running it

```
git clone <repo-url>
cd sql-mcp-agent
python -m venv venv
venv\Scripts\activate          # or source venv/bin/activate on macOS/Linux
pip install -r requirements.txt

python build.py                # generates the database, builds dbt marts,
                                # then locks the file read-only -- one command,
                                # correctly sequenced (see note below)

cd app
streamlit run dashboard.py
```

A `.env` file with `GROQ_API_KEY` (and optionally `GOOGLE_API_KEY`) is required at the project root. Both providers offer free API tiers suitable for running this project.

On the build sequence: `dbt run` needs write access to create the mart views, so the OS-level read-only flag (the filesystem layer of the security model) can't simply be applied once and left in place across rebuilds; it would block `dbt run` itself. `build.py` automates the correct order: unlock the database file, generate the seed data, build the dbt marts, then lock the file read-only, in that sequence, every time. Re-running `python build.py` is safe and fully reproducible (the data generator uses a fixed random seed), and is also how the database is regenerated after any schema or dbt model change. To unlock the file manually for any other reason, run `python db/manage_lock.py unlock`; to relock it, `python db/manage_lock.py lock`.

To run the evaluation set:

```
cd eval
python score_eval.py --category 1     # run a single category
python score_eval.py                  # run the full set
```