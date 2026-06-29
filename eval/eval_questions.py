"""
Eval set for the Text-to-SQL agent.

26 questions across 5 categories, each with a ground-truth answer
computed and verified directly against the real database (not
estimated, not assumed).

Design principles:
- Ground truth is something mechanically checkable (a row count, a
  specific value, a set of names), not "looks right to a human."
- Categories span the real difficulty gradient observed in manual
  testing: easy single-table, medium single-join, hard multi-join /
  raw-vs-mart ambiguity, deliberate grain/duplication traps, and
  aggregation/trend questions that exercise the deliberate patterns
  built into the synthetic data (tier reliability, seasonality).
- Category 4 (traps) specifically encodes the real duplication bug
  found in Stage 2 testing (fct_inventory_risk is grained per
  product-per-warehouse, not per product) -- this is not a synthetic
  example, it's an actual failure mode observed in this project.

Each entry:
  id: short identifier
  category: 1-5 (see categories below)
  question: the natural-language question to send to the agent
  expected: the ground-truth answer, as a value or set of values
  check_type: how to verify the agent's answer against `expected`
              ("exact_count", "set_match", "value_match")
  notes: why this question is in the set / what it specifically tests

Categories:
  1 = easy, single table/view, no joins
  2 = medium, single join
  3 = hard, multi-join and/or raw-table-vs-dbt-mart ambiguity
  4 = grain/duplication trap (one-to-many join correctness)
  5 = aggregation/trend (date logic, seasonality, tier patterns)
"""

EVAL_QUESTIONS = [
    # ── Category 1: easy, single table/view ──────────────────────
    {
        "id": "C1-01",
        "category": 1,
        "question": "How many suppliers are rated tier C?",
        "expected": 4,
        "check_type": "exact_count",
        "notes": "Single filter on suppliers.reliability_tier. No joins needed.",
    },
    {
        "id": "C1-02",
        "category": 1,
        "question": "How many warehouses do we have?",
        "expected": 6,
        "check_type": "exact_count",
        "notes": "Trivial COUNT(*) on warehouses.",
    },
    {
        "id": "C1-03",
        "category": 1,
        "question": "How many distinct product categories are there?",
        "expected": 7,
        "check_type": "exact_count",
        "notes": "COUNT(DISTINCT category) on products.",
    },
    {
        "id": "C1-04",
        "category": 1,
        "question": "How many purchase orders currently have a status of pending?",
        "expected": 1745,
        "check_type": "exact_count",
        "notes": "Single filter on purchase_orders.status.",
    },
    {
        "id": "C1-05",
        "category": 1,
        "question": "How many shipments have not yet been delivered?",
        "expected": 1827,
        "check_type": "exact_count",
        "notes": "Filter on shipments.actual_delivery_date IS NULL. "
                 "Tests whether the agent understands NULL = not yet delivered, "
                 "not an error/missing-data condition.",
    },
    {
        "id": "C1-06",
        "category": 1,
        "question": "How many products are in the catalog?",
        "expected": 80,
        "check_type": "exact_count",
        "notes": "Trivial COUNT(*) on products.",
    },
    {
        "id": "C1-07",
        "category": 1,
        "question": "What is the highest unit cost among all products?",
        "expected": 437.97,
        "check_type": "value_match",
        "notes": "MAX() aggregate on products.unit_cost, no grouping.",
    },
    {
        "id": "C1-08",
        "category": 1,
        "question": "How many distinct countries do our suppliers operate from?",
        "expected": 14,
        "check_type": "exact_count",
        "notes": "COUNT(DISTINCT country) on suppliers.",
    },

    # ── Category 2: medium, single join ──────────────────────────
    {
        "id": "C2-01",
        "category": 2,
        "question": "Which 3 suppliers (by name) have the highest late delivery percentage?",
        "expected": ["Barnes, Cole and Ramirez", "Garcia, Yang and Gardner", "Blake and Sons"],
        "check_type": "set_match",
        "notes": "Tests whether the agent finds and uses fct_supplier_performance "
                 "rather than re-deriving late-delivery logic from raw purchase_orders.",
    },
    {
        "id": "C2-02",
        "category": 2,
        "question": "Which warehouse region has the most customer orders?",
        "expected": "Midwest",
        "check_type": "value_match",
        "notes": "Single join: customer_orders -> warehouses, GROUP BY region.",
    },
    {
        "id": "C2-03",
        "category": 2,
        "question": "What are the top 3 product categories by number of purchase order line items?",
        "expected": ["Packaging", "Office Supplies", "Apparel"],
        "check_type": "set_match",
        "notes": "Join: purchase_order_lines -> products, GROUP BY category.",
    },
    {
        "id": "C2-04",
        "category": 2,
        "question": "Which carrier handles the most shipments?",
        "expected": "XPO Logistics",
        "check_type": "value_match",
        "notes": "Single GROUP BY on shipments.carrier, no join actually needed.",
    },
    {
        "id": "C2-05",
        "category": 2,
        "question": "Which product category has the highest average unit cost?",
        "expected": "Office Supplies",
        "check_type": "value_match",
        "notes": "GROUP BY on products.category with AVG(unit_cost).",
    },
    {
        "id": "C2-06",
        "category": 2,
        "question": "Which supplier has the most purchase orders?",
        "expected": "Johnson-Davis",
        "check_type": "value_match",
        "notes": "Join: purchase_orders -> suppliers, GROUP BY supplier_name.",
    },

    # ── Category 3: hard, multi-join / raw-vs-mart ambiguity ─────
    {
        "id": "C3-01",
        "category": 3,
        "question": "Which products are at risk of stockout, and who is the primary supplier for each?",
        "expected_distinct_product_count": 12,
        "check_type": "exact_count",
        "notes": "The hardest question in this set -- requires recognizing "
                 "fct_inventory_risk lacks supplier names, joining to products "
                 "and suppliers via primary_supplier_id.",
    },
    {
        "id": "C3-02",
        "category": 3,
        "question": "Break down the at-risk products by their supplier's reliability tier.",
        "expected": {"A": 5, "B": 5, "C": 2},
        "check_type": "value_match",
        "notes": "Three-table join (inventory_risk -> products -> suppliers) "
                 "plus a GROUP BY on a column that lives on yet another table.",
    },
    {
        "id": "C3-03",
        "category": 3,
        "question": "Which warehouse has the most late shipments, and how many?",
        "expected": ("Midwest DC", 2416),
        "check_type": "value_match",
        "notes": "Join: shipments -> customer_orders -> warehouses, plus a "
                 "late-delivery condition computed inline.",
    },
    {
        "id": "C3-04",
        "category": 3,
        "question": "Break down the at-risk products by their product category.",
        "expected": {
            "Electronics": 3, "Apparel": 3, "Office Supplies": 2,
            "Industrial Parts": 2, "Packaging": 1, "Home Goods": 1,
        },
        "check_type": "value_match",
        "notes": "Join: fct_inventory_risk -> products, GROUP BY category, "
                 "with the same distinct-product trap as C4-01/C4-02.",
    },
    {
        "id": "C3-05",
        "category": 3,
        "question": "Which warehouse has the worst average delay (in days) on late purchase orders?",
        "expected": ("West DC", 7.6),
        "check_type": "value_match",
        "notes": "Join: purchase_orders -> warehouses, filtered to late "
                 "orders only, AVG() of a computed date difference.",
    },

    # ── Category 4: grain / duplication traps ────────────────────
    {
        "id": "C4-01",
        "category": 4,
        "question": "How many distinct products are at risk of stockout?",
        "expected": 12,
        "wrong_answer_if_ungrained": 44,
        "check_type": "exact_count",
        "notes": "THE TRAP. fct_inventory_risk is grained per product-PER-"
                 "WAREHOUSE. A query without DISTINCT/GROUP BY on product_id "
                 "returns 44, not 12.",
    },
    {
        "id": "C4-02",
        "category": 4,
        "question": "How many purchase orders have been delivered?",
        "expected": 96501,
        "wrong_answer_if_ungrained": 240921,
        "check_type": "exact_count",
        "notes": "Second instance of the same grain pattern: "
                 "purchase_order_lines is grained per line item, not per order.",
    },

    # ── Category 5: aggregation / trend ──────────────────────────
    {
        "id": "C5-01",
        "category": 5,
        "question": "Which 3 months had the highest customer order volume?",
        "expected": ["2025-12", "2025-11", "2025-10"],
        "check_type": "set_match",
        "notes": "Tests date-truncation logic and the deliberate seasonal "
                 "pattern built into the synthetic data generator.",
    },
    {
        "id": "C5-02",
        "category": 5,
        "question": "What is the late delivery percentage for each supplier reliability tier?",
        "expected": {"A": 4.9, "B": 18.1, "C": 40.8},
        "check_type": "value_match",
        "notes": "Should use fct_supplier_performance directly rather than "
                 "re-deriving from raw purchase_orders+suppliers.",
    },
    {
        "id": "C5-03",
        "category": 5,
        "question": "What month did we start receiving purchase orders, and how many were placed that month?",
        "expected": ("2025-01", 5926),
        "check_type": "value_match",
        "notes": "Tests MIN(order_date) combined with a count for that period.",
    },
    {
        "id": "C5-04",
        "category": 5,
        "question": "What is the average promised lead time, in days, across all purchase orders?",
        "expected": 13.0,
        "check_type": "value_match",
        "notes": "AVG(expected_date - order_date) across the whole table.",
    },
    {
        "id": "C5-05",
        "category": 5,
        "question": "Which product category generates the most revenue from customer orders?",
        "expected": "Packaging",
        "check_type": "value_match",
        "notes": "Join: customer_order_lines -> products, GROUP BY category, "
                 "SUM(quantity * unit_price).",
    },
]


def get_questions_by_category(category: int):
    return [q for q in EVAL_QUESTIONS if q["category"] == category]


if __name__ == "__main__":
    print(f"Total eval questions: {len(EVAL_QUESTIONS)}")
    for cat in range(1, 6):
        qs = get_questions_by_category(cat)
        print(f"  Category {cat}: {len(qs)} questions")