-- fct_inventory_risk.sql
--
-- One row per product per warehouse, flagging stockout risk against
-- each product's reorder_point. The definition of "at risk" lives
-- here once, instead of being re-derived inline in every query that
-- needs to know what's low on stock.

select
    p.product_id,
    p.sku,
    p.product_name,
    p.category,
    p.reorder_point,
    w.warehouse_id,
    w.warehouse_name,
    w.region,
    i.quantity_on_hand,
    i.last_counted,
    case
        when i.quantity_on_hand < p.reorder_point then 1
        else 0
    end as is_below_reorder_point,
    p.reorder_point - i.quantity_on_hand as units_below_reorder_point,
    round(
        100.0 * (p.reorder_point - i.quantity_on_hand) / p.reorder_point, 1
    ) as pct_below_reorder_point
from {{ source('raw', 'inventory') }} i
join {{ source('raw', 'products') }} p
    on i.product_id = p.product_id
join {{ source('raw', 'warehouses') }} w
    on i.warehouse_id = w.warehouse_id