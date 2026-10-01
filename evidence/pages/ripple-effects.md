---
title: Ripple effects
---

A change to an input — an Excel upload, a CSV, an Airbyte sync, a REST poll, a webhook or a
telemetry window — is detected by the platform, and every model downstream of it re-runs in one
graph run. This page shows those changes, the runs they caused, and the model links they travelled.

## Recent changes

```sql changes
select occurred_at, title, subject, field_label, value_from, value_to, relative_delta, via_kind, via_source, status
from platform.activity
where field is not null
order by occurred_at desc
```

{#if changes.length > 0}

<DataTable data={changes} rows=20>
  <Column id=occurred_at title="When" />
  <Column id=subject title="What changed" />
  <Column id=value_from title="From" fmt="#,##0.##" />
  <Column id=value_to title="To" fmt="#,##0.##" />
  <Column id=relative_delta title="Change" fmt="pct1" contentType=delta />
  <Column id=via_kind title="Arrived via" />
  <Column id=status />
</DataTable>

{:else}

No input changes have been recorded yet.

{/if}

## Graph runs

```sql runs_by_trigger
select trigger_type, executor, status, count(*) as runs
from platform.runs
group by 1, 2, 3
order by runs desc
```

<BarChart data={runs_by_trigger} x=trigger_type y=runs series=status swapXY=true title="Runs by trigger and outcome" />

```sql recent_runs
select started_at, trigger_type, executor, status, duration_s, triggered_by
from platform.runs
order by started_at desc
limit 25
```

<DataTable data={recent_runs} rows=10>
  <Column id=started_at title="Started" />
  <Column id=trigger_type title="Trigger" />
  <Column id=executor title="Executor" />
  <Column id=status />
  <Column id=duration_s title="Seconds" fmt="#,##0.000" />
</DataTable>

## How models are linked

Each model reads datasets and writes datasets; a change travels along these links.

```sql links
select model, direction, dataset, fields
from platform.model_links
order by model, direction desc, dataset
```

<DataTable data={links} rows=30 groupBy=model>
  <Column id=model />
  <Column id=direction />
  <Column id=dataset />
  <Column id=fields title="Fields" />
</DataTable>
