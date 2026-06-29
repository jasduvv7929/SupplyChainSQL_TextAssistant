"""
Pre-agent guardrail: a cheap, tool-free LLM classification call that
checks whether a question is answerable using the supply chain
database, before committing to the full multi-step agent loop.

Why this exists: the system prompt alone is not a reliable guardrail
(measured during Stage 2/3 development -- a grain-deduplication
instruction in the system prompt only worked 2/3 times across
repeated trials). An off-topic or unanswerable question given
directly to the full agent risks the same failure mode observed on
the hardest in-scope questions: the model may hallucinate a
plausible-sounding but wrong table/column rather than cleanly
refusing, since it has no correct path forward either way.

Design choices:
- No tools bound to this call. It's pure classification; binding
  tools here would re-introduce the malformed-tool-call risk for a
  task that doesn't need tool use at all.
- Forces a single-word YES/NO response to keep token cost and parsing
  ambiguity low.
- FAILS OPEN: if the classification call itself errors (rate limit,
  malformed response), the question is allowed through to the main
  agent rather than blocked. The main agent already has its own
  error handling (Stage 3's retry loop); over-blocking on a
  classifier hiccup would hurt usability more than an occasional
  off-topic question reaching the agent would hurt correctness.
"""

import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

CLASSIFIER_PROMPT_TEMPLATE = """You are a strict binary classifier, not a conversational assistant.

The database covers ONLY: suppliers, warehouses, products, inventory levels, purchase orders, customer orders, shipments, and related supply chain metrics (late deliveries, stockout risk, reliability tiers, lead times, revenue by category).

Question: "{question}"

Is this question answerable using ONLY that supply chain database? Respond with EXACTLY one word: YES or NO. No explanation, no punctuation, just the single word."""


async def is_in_scope(question: str) -> dict:
    """
    Returns {"in_scope": bool, "classifier_succeeded": bool, "raw_response": str | None}

    classifier_succeeded=False means the classification call itself
    failed (rate limit, malformed response) -- per the fail-open
    design, callers should treat this the same as in_scope=True.
    """
    try:
        llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
        response = await llm.ainvoke(CLASSIFIER_PROMPT_TEMPLATE.format(question=question))
        raw = response.content.strip().upper()

        if raw.startswith("YES"):
            return {"in_scope": True, "classifier_succeeded": True, "raw_response": raw}
        elif raw.startswith("NO"):
            return {"in_scope": False, "classifier_succeeded": True, "raw_response": raw}
        else:
            # Malformed/unexpected response (not a clean YES/NO) --
            # fail open rather than guess.
            return {"in_scope": True, "classifier_succeeded": False, "raw_response": raw}

    except Exception as e:
        # Fail open: let the question through to the main agent.
        return {"in_scope": True, "classifier_succeeded": False, "raw_response": f"ERROR: {str(e)[:100]}"}


if __name__ == "__main__":
    import asyncio

    async def test():
        test_questions = [
            "Which suppliers have the highest late delivery rate?",
            "What's the capital of France?",
            "Write me a haiku about clouds.",
            "How many products are below reorder point?",
        ]
        for q in test_questions:
            result = await is_in_scope(q)
            print(f"[{result['in_scope']}] {q!r} -> {result['raw_response']}")

    asyncio.run(test())