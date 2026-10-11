-- Revenue = sum of SETTLED amounts per UTC day and currency.
-- FAILED and PENDING are excluded. REFUNDED events are reported in their own columns and
-- are NOT subtracted: the event stream carries no separate refund amount to net against.
-- Currencies are never converted or summed together.
select
    event_date as revenue_date,
    currency,
    sum(amount) filter (where status = 'SETTLED') as settled_amount,
    count(*) filter (where status = 'SETTLED') as settled_count,
    coalesce(sum(amount) filter (where status = 'REFUNDED'), 0) as refunded_amount,
    count(*) filter (where status = 'REFUNDED') as refunded_count,
    count(*) filter (where status = 'FAILED') as failed_count,
    current_timestamp as _refreshed_at
from {{ ref('stg_accepted_transactions') }}
group by 1, 2
having count(*) filter (where status = 'SETTLED') > 0
