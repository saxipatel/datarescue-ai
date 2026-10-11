-- Singular test: mart totals must equal the settled sum in staging (no rows lost or doubled).
with mart as (select coalesce(sum(settled_amount), 0) as total from {{ ref('mart_daily_revenue') }}),
     stg as (
         select coalesce(sum(amount), 0) as total
         from {{ ref('stg_accepted_transactions') }}
         where status = 'SETTLED'
     )
select mart.total as mart_total, stg.total as staging_total
from mart, stg
where mart.total <> stg.total
