-- One row per accepted event, with the source name attached. Amounts stay NUMERIC.
select
    t.event_id,
    t.transaction_id,
    t.customer_id,
    t.merchant_id,
    t.amount,
    t.currency,
    t.status,
    t.event_time,
    (t.event_time at time zone 'UTC')::date as event_date,
    s.name as source_name,
    t.batch_id,
    t.recovery_job_id is not null as recovered,
    t.created_at as accepted_at
from {{ source('datarescue', 'accepted_transactions') }} as t
join {{ source('datarescue', 'source_systems') }} as s on s.id = t.source_id
