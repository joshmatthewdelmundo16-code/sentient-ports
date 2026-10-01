---
title: Master plan
---

Every period of a master plan is a real engine run in the platform; these are its recorded
outputs, per plan and year.

```sql metrics
select distinct metric from platform.plan_periods order by metric
```

{#if metrics.length > 0}

<Dropdown data={metrics} name=metric value=metric defaultValue="capacity_headroom_pct" title="Indicator" />

```sql series
select plan, year, value
from platform.plan_periods
where metric = '${inputs.metric.value}'
order by plan, year
```

<LineChart data={series} x=year y=value series=plan xFmt="0" title="{inputs.metric.value} by year" />

```sql final_year
with last as (select plan, max(year) as year from platform.plan_periods group by plan)
select p.plan, p.year, p.value
from platform.plan_periods p join last using (plan, year)
where p.metric = '${inputs.metric.value}'
order by p.value desc
```

<DataTable data={final_year}>
  <Column id=plan />
  <Column id=year title="Final year" fmt="0" />
  <Column id=value title="{inputs.metric.value}" fmt="#,##0.0" />
</DataTable>

{:else}

No master plans have been evaluated in this organization yet.

{/if}
