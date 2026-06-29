"""
Eval scoring script.

Methodology: Execution Accuracy (EX), the standard metric used by the
Spider and BIRD text-to-SQL benchmarks. Comparing raw SQL strings is
"too strict" (two differently-written but equivalent queries won't
string-match), so the standard approach is: extract the agent's
generated SQL, run it ourselves against the real database, and
compare the ACTUAL RESULT to a ground-truth answer -- not the
agent's prose summary, which varies in wording and is unreliable to
parse.

Designed for sparse, deliberate runs given free-tier token limits --
supports running a single category, a specific question ID, or a
random sample, rather than forcing a full 26-question run every time.
"""

import argparse
import asyncio
import os
import random
import sqlite3
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_questions import EVAL_QUESTIONS, get_questions_by_category

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
from agent_with_retry import ask_with_self_correction

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "supply_chain.db")


def extract_last_sql(full_trace):
    """Pull the SQL string from the agent's last run_query tool call."""
    if not full_trace:
        return None
    last_sql = None
    for msg in full_trace:
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                if tc["name"] == "run_query":
                    last_sql = tc["args"].get("sql")
    return last_sql


def run_sql_directly(sql: str):
    """Execute SQL ourselves (read-only) to get the actual result."""
    if not sql:
        return None, "No SQL found in trace"
    try:
        uri = f"file:{DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        conn.close()
        return {"columns": columns, "rows": rows}, None
    except sqlite3.Error as e:
        return None, str(e)


def score_exact_count(actual_result, expected):
    if actual_result is None or not actual_result["rows"]:
        return False, "No rows returned"
    try:
        actual_value = actual_result["rows"][0][0]
        return int(actual_value) == int(expected), f"got {actual_value}, expected {expected}"
    except (ValueError, TypeError, IndexError):
        return False, f"Could not parse a count from {actual_result['rows'][0]}"


def score_value_match(actual_result, expected):
    if actual_result is None or not actual_result["rows"]:
        return False, "No rows returned"

    if isinstance(expected, dict):
        actual_map = {}
        for row in actual_result["rows"]:
            if len(row) >= 2:
                actual_map[str(row[0])] = row[1]
        mismatches = []
        for k, v in expected.items():
            if k not in actual_map:
                mismatches.append(f"missing key '{k}'")
            elif isinstance(v, (int, float)) and isinstance(actual_map[k], (int, float)):
                if abs(actual_map[k] - v) > 0.5:
                    mismatches.append(f"{k}: got {actual_map[k]}, expected {v}")
            elif actual_map[k] != v:
                mismatches.append(f"{k}: got {actual_map[k]}, expected {v}")
        return len(mismatches) == 0, "; ".join(mismatches) if mismatches else "match"

    if isinstance(expected, tuple):
        row = actual_result["rows"][0]
        mismatches = []
        for i, exp_val in enumerate(expected):
            if i >= len(row):
                mismatches.append(f"missing column {i}")
                continue
            if isinstance(exp_val, (int, float)) and isinstance(row[i], (int, float)):
                if abs(row[i] - exp_val) > 0.5:
                    mismatches.append(f"col{i}: got {row[i]}, expected {exp_val}")
            elif str(row[i]) != str(exp_val):
                mismatches.append(f"col{i}: got {row[i]}, expected {exp_val}")
        return len(mismatches) == 0, "; ".join(mismatches) if mismatches else "match"

    actual_value = actual_result["rows"][0][0]
    if isinstance(expected, (int, float)) and isinstance(actual_value, (int, float)):
        match = abs(actual_value - expected) <= 0.5
    else:
        match = str(actual_value) == str(expected)
    return match, f"got {actual_value}, expected {expected}"


def score_set_match(actual_result, expected):
    if actual_result is None or not actual_result["rows"]:
        return False, "No rows returned"
    actual_values = {str(row[0]) for row in actual_result["rows"]}
    expected_values = {str(v) for v in expected}
    match = actual_values == expected_values
    return match, f"got {actual_values}, expected {expected_values}"


SCORERS = {
    "exact_count": score_exact_count,
    "value_match": score_value_match,
    "set_match": score_set_match,
}


async def run_eval(questions, verbose=True, delay_seconds=5, results_path="eval_results.json"):
    """
    delay_seconds: a modest proactive pause between questions to
    reduce how often the per-minute token cap is hit in the first
    place. This is now a complement to, not a replacement for,
    agent_with_retry.py's reactive handling, which reads Groq's
    actual specified wait time and retries automatically when a rate
    limit is hit despite this delay.

    results_path: results are saved incrementally after EVERY question,
    not just at the end, so a mid-run rate-limit crash doesn't lose
    everything already collected. Re-running the script overwrites
    this file; if resuming a partial run matters later, this would
    need a --resume flag that skips already-completed IDs, which is
    not implemented yet.
    """
    results = []
    for i, q in enumerate(questions):
        print(f"\n[{q['id']}] {q['question']}")
        agent_result = await ask_with_self_correction(q["question"], verbose=verbose)

        if not agent_result["success"]:
            print(f"  AGENT FAILED: {agent_result['error']}")
            results.append({"id": q["id"], "category": q["category"], "passed": False,
                             "reason": f"Agent failed: {agent_result['error']}"})
        else:
            sql = extract_last_sql(agent_result["full_trace"])
            actual_result, sql_error = run_sql_directly(sql)

            if sql_error:
                print(f"  SQL RE-RUN FAILED: {sql_error}")
                print(f"  SQL was: {sql}")
                results.append({"id": q["id"], "category": q["category"], "passed": False,
                                 "reason": f"SQL error on re-run: {sql_error}", "sql": sql})
            else:
                expected = q.get("expected", q.get("expected_distinct_product_count"))
                scorer = SCORERS[q["check_type"]]
                passed, detail = scorer(actual_result, expected)
                status = "PASS" if passed else "FAIL"
                print(f"  {status}: {detail}")
                print(f"  SQL: {sql}")
                results.append({"id": q["id"], "category": q["category"], "passed": passed,
                                 "reason": detail, "sql": sql})

        # Save after every question, not just at the end.
        with open(results_path, "w") as f:
            json.dump(results, f, indent=2)

        # Pause between questions (skip after the last one).
        if i < len(questions) - 1:
            if verbose:
                print(f"  (waiting {delay_seconds}s before next question...)")
            await asyncio.sleep(delay_seconds)

    return results


def print_summary(results):
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    print(f"Overall: {passed}/{total} ({100*passed/total:.1f}%)" if total else "No questions run.")

    for cat in sorted(set(r["category"] for r in results)):
        cat_results = [r for r in results if r["category"] == cat]
        cat_passed = sum(1 for r in cat_results if r["passed"])
        print(f"  Category {cat}: {cat_passed}/{len(cat_results)}")

    failed = [r for r in results if not r["passed"]]
    if failed:
        print("\nFailed questions:")
        for r in failed:
            print(f"  [{r['id']}] {r['reason']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the text-to-SQL eval set.")
    parser.add_argument("--category", type=int, help="Run only this category (1-5)")
    parser.add_argument("--id", type=str, help="Run only this specific question ID (e.g. C1-01)")
    parser.add_argument("--sample", type=int, help="Run a random sample of N questions")
    args = parser.parse_args()

    if args.id:
        questions = [q for q in EVAL_QUESTIONS if q["id"] == args.id]
    elif args.category:
        questions = get_questions_by_category(args.category)
    elif args.sample:
        questions = random.sample(EVAL_QUESTIONS, min(args.sample, len(EVAL_QUESTIONS)))
    else:
        questions = EVAL_QUESTIONS

    print(f"Running {len(questions)} question(s)...")
    results = asyncio.run(run_eval(questions))
    print_summary(results)