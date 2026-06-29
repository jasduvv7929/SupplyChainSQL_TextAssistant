-- fct_supplier_performance.sql
--
-- One row per supplier, pre-aggregating delivery performance from
-- purchase_orders. This exists so the agent (and any human analyst)
-- never has to re-derive "what counts as late" from raw tables every
-- time someone asks a supplier-reliability question. The definition
-- of "late" lives here, in exactly one place.

with delivered_orders as (
    select
        po_id,
        supplier_id,
        warehouse_id,
        order_date,
        expected_date,
        actual_delivery_date,
        case
            when actual_delivery_date > expected_date then 1
            else 0
        end as is_late,
        case
            when actual_delivery_date > expected_date
                then julianday(actual_delivery_date) - julianday(expected_date)
            else 0
        end as days_late
    from {{ source('raw', 'purchase_orders') }}
    where status = 'delivered'
)

select
    s.supplier_id,
    s.supplier_name,
    s.country,
    s.reliability_tier,
    count(d.po_id) as total_delivered_orders,
    sum(d.is_late) as late_orders,
    round(100.0 * sum(d.is_late) / count(d.po_id), 1) as late_delivery_pct,
    round(avg(d.days_late), 1) as avg_days_late_when_late,
    round(avg(julianday(d.expected_date) - julianday(d.order_date)), 1) as avg_promised_lead_time_days
from {{ source('raw', 'suppliers') }} s
left join delivered_orders d
    on s.supplier_id = d.supplier_id
group by s.supplier_id, s.supplier_name, s.country, s.reliability_tier