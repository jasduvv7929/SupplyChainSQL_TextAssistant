"""
FastMCP server exposing the supply chain SQLite database (raw tables +
dbt mart views) to an MCP client.

Security model -- three independent layers, each a backstop if the
layer above somehow fails:

  Layer 1 (connection-level): the database is opened with SQLite's
  URI mode=ro flag, so the SQLite C library itself physically refuses
  any INSERT/UPDATE/DROP, regardless of what Python code does.

  Layer 2 (filesystem-level): the .db file itself is set read-only at
  the OS level, so even a completely different process/bug touching
  the same file can't write to it.

  Layer 3 (application-level): every incoming query is parsed into an
  AST with sqlglot before being sent to SQLite at all, and rejected
  unless it's exactly one statement rooted at SELECT or WITH. This is
  what specifically blocks stacked-query attacks like
  "SELECT 1; DROP TABLE products;", which a naive "does the string
  start with SELECT" check would NOT catch.

Tools exposed:
  - list_tables    : what tables/views exist
  - describe_table  : columns + types for one table/view
  - run_query       : execute a validated, read-only SELECT/WITH query
  - run_explain     : return the query plan for a query, without running it
"""

from fastmcp import FastMCP
import sqlite3
import sqlglot
from sqlglot import exp

# ── Configuration ──────────────────────────────────────────────────
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_THIS_DIR, "..", "db", "supply_chain.db")
DB_PATH = os.path.abspath(DB_PATH)  # normalize ../ for cleaner error messages

# Layer 1: connection-level read-only enforcement via SQLite URI mode.
# uri=True is required for the "file:...?mode=ro" syntax to be parsed
# as a URI instead of a literal filename.
def get_readonly_connection():
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    return conn


# ── Layer 3: AST-based query validation ─────────────────────────────
def validate_select_only(sql: str) -> tuple[bool, str]:
    """
    Returns (is_valid, error_message).
    Parses the SQL into an AST and rejects anything that isn't exactly
    one statement rooted at SELECT or WITH (a CTE that resolves to a
    SELECT). This specifically blocks stacked queries like
    "SELECT 1; DROP TABLE x;" that a string-prefix check would miss,
    since sqlglot.parse() returns one AST node per statement, not one
    blob of text.
    """
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except Exception as e:
        return False, f"SQL failed to parse: {e}"

    # Remove None entries (sqlglot can return None for empty statements,
    # e.g. a trailing semicolon)
    statements = [s for s in statements if s is not None]

    if len(statements) == 0:
        return False, "No valid SQL statement found."
    if len(statements) > 1:
        return False, (
            f"Only one SQL statement is allowed per request, found "
            f"{len(statements)}. Stacked queries are not permitted."
        )

    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.With)):
        return False, (
            f"Only SELECT or WITH (CTE) statements are allowed. "
            f"Got: {type(stmt).__name__}"
        )

    return True, ""


# ── MCP server ────────────────────────────────────────────────────
mcp = FastMCP("supply-chain-sql")


@mcp.tool()
def list_tables() -> list[str]:
    """List all tables and views available to query, including both
    raw OLTP tables and dbt-built mart views (fct_supplier_performance,
    fct_inventory_risk)."""
    conn = get_readonly_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


@mcp.tool()
def describe_table(table_name: str) -> list[dict]:
    """Return column name, type, and nullability for a given table or
    view. Call this before writing a query against an unfamiliar table."""
    valid_tables = set(list_tables())
    if table_name not in valid_tables:
        return [{"error": f"Unknown table or view: '{table_name}'. "
                           f"Call list_tables() to see valid options."}]

    conn = get_readonly_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info('{table_name}')")
        columns = cur.fetchall()

        # PRAGMA table_info reliably reports declared types for real
        # tables (since CREATE TABLE specifies them), but for views it
        # often returns an empty string -- a view's "columns" are just
        # whatever expressions appear in its SELECT, and SQLite doesn't
        # always persist an inferred type for computed expressions
        # (e.g. COUNT(...), ROUND(...)) the way it does for a literal
        # column. To fill that gap, we sample one real row from the
        # table/view and check Python's runtime type of each value,
        # which reflects what SQLite actually stored, regardless of
        # what PRAGMA could determine ahead of time.
        sample_types = {}
        cur.execute(f"SELECT * FROM '{table_name}' LIMIT 1")
        sample_row = cur.fetchone()
        if sample_row is not None:
            sample_columns = [desc[0] for desc in cur.description]
            for col_name, val in zip(sample_columns, sample_row):
                if val is not None:
                    sample_types[col_name] = type(val).__name__

        result = []
        for col in columns:
            declared_type = col[2]
            col_name = col[1]
            if not declared_type:
                # Fall back to the sampled runtime type, map Python's
                # type names to SQL-style names so the agent sees
                # familiar terminology either way.
                py_type = sample_types.get(col_name)
                type_map = {"int": "INTEGER", "float": "REAL", "str": "TEXT"}
                declared_type = type_map.get(py_type, "UNKNOWN (no non-null sample)")
            result.append({
                "column_name": col_name,
                "type": declared_type,
                "not_null": bool(col[3]),
                "is_primary_key": bool(col[5]),
            })
        return result
    finally:
        conn.close()

@mcp.tool()
def run_query(sql: str) -> dict:
    """Execute a read-only SELECT (or WITH/CTE) query against the
    supply chain database and return the results. Only single SELECT
    statements are permitted -- write operations and multi-statement
    queries are rejected before they ever reach the database."""
    is_valid, error = validate_select_only(sql)
    if not is_valid:
        return {"error": error, "rows": [], "columns": []}

    conn = get_readonly_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchall()
        MAX_ROWS = 500
        truncated = len(rows) > MAX_ROWS

        # Run EXPLAIN QUERY PLAN on a separate cursor to avoid any
        # state interference with the cursor that ran the actual query.
        try:
            explain_cur = conn.cursor()
            explain_cur.execute(f"EXPLAIN QUERY PLAN {sql}")
            plan_rows = explain_cur.fetchall()
            plan = [
                {"id": r[0], "parent": r[1], "notused": r[2], "detail": r[3]}
                for r in plan_rows
            ]
            contains_full_scan = any("SCAN" in r["detail"] for r in plan)
        except Exception:
            plan = []
            contains_full_scan = False

        return {
            "columns": columns,
            "rows": rows[:MAX_ROWS],
            "row_count": len(rows),
            "truncated": truncated,
            "query_plan": plan,
            "contains_full_scan": contains_full_scan,
            "scan_warning": (
                "WARNING: This query performs a full table scan on one or more "
                "large tables. Consider rewriting with more selective filters or "
                "checking whether an index exists on the filtered columns."
                if contains_full_scan else None
            ),
        }
    except sqlite3.Error as e:
        return {"error": str(e), "rows": [], "columns": []}
    finally:
        conn.close()


@mcp.tool()
def run_explain(sql: str) -> dict:
    """Return the query plan (EXPLAIN QUERY PLAN) for a SELECT query,
    without executing it. Use this to check whether a query relies on
    a full table SCAN versus an indexed SEARCH before running it for
    real, especially on the larger fact tables."""
    is_valid, error = validate_select_only(sql)
    if not is_valid:
        return {"error": error, "plan": []}

    conn = get_readonly_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"EXPLAIN QUERY PLAN {sql}")
        plan_rows = cur.fetchall()
        plan = [
            {"id": row[0], "parent": row[1], "notused": row[2], "detail": row[3]}
            for row in plan_rows
        ]
        has_scan = any("SCAN" in row["detail"] for row in plan)
        return {"plan": plan, "contains_full_scan": has_scan}
    except sqlite3.Error as e:
        return {"error": str(e), "plan": []}
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        port = int(os.environ.get("PORT", 8000))
        print(f"Starting FastMCP server on HTTP port {port}", flush=True)
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        mcp.run()