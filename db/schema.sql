PRAGMA foreign_keys = ON;

CREATE TABLE suppliers (
    supplier_id     INTEGER PRIMARY KEY,
    supplier_name   TEXT NOT NULL,
    country         TEXT NOT NULL,
    reliability_tier TEXT CHECK (reliability_tier IN ('A', 'B', 'C')) NOT NULL,
    contract_start  DATE NOT NULL
);

CREATE TABLE warehouses (
    warehouse_id    INTEGER PRIMARY KEY,
    warehouse_name  TEXT NOT NULL,
    region          TEXT NOT NULL,
    capacity_units  INTEGER NOT NULL
);

CREATE TABLE products (
    product_id      INTEGER PRIMARY KEY,
    sku             TEXT NOT NULL UNIQUE,
    product_name    TEXT NOT NULL,
    category        TEXT NOT NULL,
    unit_cost       REAL NOT NULL,
    reorder_point   INTEGER NOT NULL,
    primary_supplier_id INTEGER NOT NULL,
    FOREIGN KEY (primary_supplier_id) REFERENCES suppliers(supplier_id)
);

CREATE TABLE inventory (
    inventory_id    INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL,
    warehouse_id    INTEGER NOT NULL,
    quantity_on_hand INTEGER NOT NULL,
    last_counted    DATE NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(product_id),
    FOREIGN KEY (warehouse_id) REFERENCES warehouses(warehouse_id),
    UNIQUE (product_id, warehouse_id)
);

CREATE TABLE purchase_orders (
    po_id           INTEGER PRIMARY KEY,
    supplier_id     INTEGER NOT NULL,
    warehouse_id    INTEGER NOT NULL,
    order_date      DATE NOT NULL,
    expected_date   DATE NOT NULL,
    actual_delivery_date DATE,
    status          TEXT CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled')) NOT NULL,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id),
    FOREIGN KEY (warehouse_id) REFERENCES warehouses(warehouse_id)
);

CREATE TABLE purchase_order_lines (
    po_line_id      INTEGER PRIMARY KEY,
    po_id           INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    quantity_ordered INTEGER NOT NULL,
    unit_price      REAL NOT NULL,
    FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE customer_orders (
    order_id        INTEGER PRIMARY KEY,
    customer_name   TEXT NOT NULL,
    warehouse_id    INTEGER NOT NULL,
    order_date      DATE NOT NULL,
    FOREIGN KEY (warehouse_id) REFERENCES warehouses(warehouse_id)
);

CREATE TABLE customer_order_lines (
    order_line_id   INTEGER PRIMARY KEY,
    order_id        INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    quantity_ordered INTEGER NOT NULL,
    unit_price      REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES customer_orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE shipments (
    shipment_id     INTEGER PRIMARY KEY,
    order_id        INTEGER NOT NULL,
    ship_date       DATE NOT NULL,
    expected_delivery_date DATE NOT NULL,
    actual_delivery_date   DATE,
    carrier         TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES customer_orders(order_id)
);

CREATE INDEX idx_inventory_product ON inventory(product_id);
CREATE INDEX idx_inventory_warehouse ON inventory(warehouse_id);
CREATE INDEX idx_po_supplier ON purchase_orders(supplier_id);
CREATE INDEX idx_po_warehouse ON purchase_orders(warehouse_id);
CREATE INDEX idx_poline_po ON purchase_order_lines(po_id);
CREATE INDEX idx_poline_product ON purchase_order_lines(product_id);
CREATE INDEX idx_corder_warehouse ON customer_orders(warehouse_id);
CREATE INDEX idx_coline_order ON customer_order_lines(order_id);
CREATE INDEX idx_coline_product ON customer_order_lines(product_id);
CREATE INDEX idx_shipment_order ON shipments(order_id);