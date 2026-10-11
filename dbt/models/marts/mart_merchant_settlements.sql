-- Per-merchant settlement totals by currency. Same revenue semantics as mart_daily_revenue.
select
    merchant_id,
    currency,
    sum(amount) filter (where status = 'SETTLED') as settled_amount,
    count(*) filter (where status = 'SETTLED') as settled_count,
    coalesce(sum(amount) filter (where status = 'REFUNDED'), 0) as refunded_amount,
    count(*) filter (where status = 'REFUNDED') as refunded_count,
    min(event_time) as first_event_at,
    max(event_time) as last_event_at,
    current_timestamp as _refreshed_at
from {{ ref('stg_accepted_transactions') }}
group by 1, 2
having count(*) filter (where status = 'SETTLED') > 0
