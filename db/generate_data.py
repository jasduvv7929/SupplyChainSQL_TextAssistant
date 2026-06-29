"""
Generate synthetic supply chain seed data for the SQLite database, at scale.

Scaled up from the original small version specifically so that
EXPLAIN QUERY PLAN differences (SCAN vs SEARCH) translate into a
measurable, citable timing difference, not just a different label in
the query plan output.

Deliberate design choices (unchanged from the smaller version):
- Supplier reliability_tier actually drives late-delivery probability and
  delay magnitude, so "which suppliers are unreliable" has a real,
  verifiable answer in the data.
- A known subset of products is pushed below their reorder_point.
- Order volume varies mildly by month (soft seasonal multiplier).
- Fixed random seed (42) makes this reproducible.

Performance note: at this scale, row-by-row Python object creation in
a loop is the bottleneck, not SQLite itself. We build plain tuples in
list comprehensions wherever possible and insert in large batches via
executemany.
"""

import sqlite3
import random
import time
from datetime import date, timedelta
from faker import Faker
import os

random.seed(42)
fake = Faker()
Faker.seed(42)

DB_PATH = "supply_chain.db"
SCHEMA_PATH = "schema.sql"

N_SUPPLIERS = 15
N_WAREHOUSES = 6
N_PRODUCTS = 80
N_PURCHASE_ORDERS = 100_000
N_CUSTOMER_ORDERS = 120_000

START_DATE = date(2025, 1, 1)
END_DATE = date(2026, 6, 1)

CATEGORIES = ["Electronics", "Apparel", "Home Goods", "Industrial Parts",
              "Packaging", "Food & Beverage", "Office Supplies"]
REGIONS = ["Northeast", "Southeast", "Midwest", "Southwest", "West", "Pacific Northwest"]
CARRIERS = ["FedEx Freight", "UPS Ground", "DHL Supply Chain", "XPO Logistics", "Old Dominion"]

TIER_LATE_PROB = {"A": 0.05, "B": 0.18, "C": 0.40}
TIER_DELAY_DAYS = {"A": 2, "B": 5, "C": 11}

MONTH_SEASONALITY = {
    1: 0.85, 2: 0.85, 3: 0.95, 4: 1.0, 5: 1.0, 6: 1.05,
    7: 0.95, 8: 0.95, 9: 1.05, 10: 1.15, 11: 1.35, 12: 1.4,
}


def random_date(start: date, end: date) -> date:
    delta_days = (end - start).days
    return start + timedelta(days=random.randint(0, delta_days))


def build():
    t0 = time.perf_counter()

    # If a database already exists from a previous run, remove it
    # first rather than layering new CREATE TABLE statements onto an
    # existing schema (which fails with "table already exists").
    # This guarantees a truly fresh, reproducible rebuild every time,
    # which is the actual property we want: anyone should be able to
    # delete this file and regenerate an identical database from the
    # fixed seed, with no dependency on what state the file was
    # previously in.
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Removed existing {DB_PATH} before rebuilding.")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    with open(SCHEMA_PATH) as f:
        cur.executescript(f.read())

    tiers = ["A"] * 4 + ["B"] * 7 + ["C"] * 4
    random.shuffle(tiers)
    suppliers = [
        (i, fake.company(), fake.country(), tiers[i - 1],
         random_date(date(2022, 1, 1), date(2024, 6, 1)).isoformat())
        for i in range(1, N_SUPPLIERS + 1)
    ]
    cur.executemany("INSERT INTO suppliers VALUES (?,?,?,?,?)", suppliers)

    warehouses = [
        (i, f"{REGIONS[i-1]} DC", REGIONS[i - 1], random.randint(50000, 200000))
        for i in range(1, N_WAREHOUSES + 1)
    ]
    cur.executemany("INSERT INTO warehouses VALUES (?,?,?,?)", warehouses)

    products = []
    for i in range(1, N_PRODUCTS + 1):
        unit_cost = round(random.uniform(2.5, 450.0), 2)
        products.append((
            i, f"SKU-{1000 + i}", fake.catch_phrase().title()[:40],
            random.choice(CATEGORIES), unit_cost,
            random.randint(20, 300), random.randint(1, N_SUPPLIERS),
        ))
    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", products)

    stockout_risk_products = set(random.sample(range(1, N_PRODUCTS + 1), 12))

    inventory = []
    inv_id = 1
    for p in products:
        product_id, reorder_point = p[0], p[5]
        for w in warehouses:
            warehouse_id = w[0]
            if product_id in stockout_risk_products and random.random() < 0.6:
                qty = random.randint(0, max(reorder_point - 5, 0))
            else:
                qty = random.randint(reorder_point, reorder_point * 4)
            inventory.append((
                inv_id, product_id, warehouse_id, qty,
                random_date(date(2026, 5, 1), date(2026, 6, 1)).isoformat(),
            ))
            inv_id += 1
    cur.executemany("INSERT INTO inventory VALUES (?,?,?,?,?)", inventory)

    t_dims = time.perf_counter()
    print(f"Dimension tables done in {t_dims - t0:.1f}s")

    supplier_ids_tiers = [(s[0], s[3]) for s in suppliers]
    warehouse_ids = [w[0] for w in warehouses]
    product_ids_costs = [(p[0], p[4]) for p in products]

    purchase_orders = []
    po_lines = []
    po_line_id = 1

    for po_id in range(1, N_PURCHASE_ORDERS + 1):
        supplier_id, tier = random.choice(supplier_ids_tiers)
        warehouse_id = random.choice(warehouse_ids)
        order_date = random_date(START_DATE, END_DATE)
        lead_time = random.randint(5, 21)
        expected_date = order_date + timedelta(days=lead_time)

        is_late = random.random() < TIER_LATE_PROB[tier]
        if order_date > date(2026, 6, 1) - timedelta(days=lead_time + 5):
            status = random.choice(["pending", "shipped"])
            actual_delivery_date = None
        else:
            status = "delivered"
            delay = 0
            if is_late:
                delay = max(1, int(random.gauss(TIER_DELAY_DAYS[tier], 3)))
            actual_delivery_date = (expected_date + timedelta(days=delay)).isoformat()

        purchase_orders.append((
            po_id, supplier_id, warehouse_id, order_date.isoformat(),
            expected_date.isoformat(), actual_delivery_date, status,
        ))

        n_lines = random.randint(1, 4)
        for _ in range(n_lines):
            prod_id, prod_cost = random.choice(product_ids_costs)
            po_lines.append((
                po_line_id, po_id, prod_id,
                random.randint(50, 800),
                round(prod_cost * random.uniform(0.9, 1.0), 2),
            ))
            po_line_id += 1

    cur.executemany("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?)", purchase_orders)
    cur.executemany("INSERT INTO purchase_order_lines VALUES (?,?,?,?,?)", po_lines)
    conn.commit()

    t_po = time.perf_counter()
    print(f"Purchase orders + lines ({len(purchase_orders)} / {len(po_lines)} rows) done in {t_po - t_dims:.1f}s")

    customer_orders = []
    coline_rows = []
    shipments = []
    coline_id = 1
    shipment_id = 1

    all_days = [START_DATE + timedelta(days=x) for x in range((END_DATE - START_DATE).days)]
    weights = [MONTH_SEASONALITY[d.month] for d in all_days]
    sampled_dates = random.choices(all_days, weights=weights, k=N_CUSTOMER_ORDERS)

    for order_id in range(1, N_CUSTOMER_ORDERS + 1):
        order_date = sampled_dates[order_id - 1]
        warehouse_id = random.choice(warehouse_ids)
        customer_orders.append((order_id, fake.name(), warehouse_id, order_date.isoformat()))

        n_lines = random.randint(1, 3)
        for _ in range(n_lines):
            prod_id, prod_cost = random.choice(product_ids_costs)
            coline_rows.append((
                coline_id, order_id, prod_id,
                random.randint(1, 25),
                round(prod_cost * random.uniform(1.15, 1.6), 2),
            ))
            coline_id += 1

        ship_date = order_date + timedelta(days=random.randint(1, 4))
        carrier_late_prob = 0.12
        transit_days = random.randint(2, 7)
        expected_delivery = ship_date + timedelta(days=transit_days)

        if ship_date > date(2026, 6, 1) - timedelta(days=transit_days + 2):
            actual_delivery = None
        else:
            delay = 0
            if random.random() < carrier_late_prob:
                delay = random.randint(1, 6)
            actual_delivery = (expected_delivery + timedelta(days=delay)).isoformat()

        shipments.append((
            shipment_id, order_id, ship_date.isoformat(),
            expected_delivery.isoformat(), actual_delivery,
            random.choice(CARRIERS),
        ))
        shipment_id += 1

    cur.executemany("INSERT INTO customer_orders VALUES (?,?,?,?)", customer_orders)
    cur.executemany("INSERT INTO customer_order_lines VALUES (?,?,?,?,?)", coline_rows)
    cur.executemany("INSERT INTO shipments VALUES (?,?,?,?,?,?)", shipments)
    conn.commit()

    t_co = time.perf_counter()
    print(f"Customer orders + lines + shipments ({len(customer_orders)} / {len(coline_rows)} / {len(shipments)} rows) done in {t_co - t_po:.1f}s")

    print()
    for table in ["suppliers", "warehouses", "products", "inventory",
                  "purchase_orders", "purchase_order_lines",
                  "customer_orders", "customer_order_lines", "shipments"]:
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table:25s} {n:7d} rows")

    conn.close()

    size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"\nDatabase file size: {size_mb:.1f} MB")
    print(f"Total time: {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    build()