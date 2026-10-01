---
title: Port decision overview
---

```sql meta
select * from platform.export_meta
```

<Alert status="info">
Exported from the platform for <b>{meta[0]?.organization_name ?? 'an organization'}</b> by {meta[0]?.exported_by ?? 'unknown'} at {meta[0]?.exported_at ?? 'unknown time'}.
Everything on these pages came through the platform's API under that user's access — no database was read directly.
Models are illustrative and parameters synthetic unless your organization says otherwise.
</Alert>

## Where the federation stands now

```sql counts
select
  (select count(*) from platform.datasets) as datasets,
  (select count(distinct model) from platform.model_links) as models,
  (select count(*) from platform.runs where status = 'succeeded') as succeeded_runs,
  (select count(*) from platform.runs where status = 'failed') as failed_runs
```

<BigValue data={counts} value=models title="Linked models" />
<BigValue data={counts} value=datasets title="Datasets" />
<BigValue data={counts} value=succeeded_runs title="Succeeded runs" />
<BigValue data={counts} value=failed_runs title="Failed runs" />

## Current decision indicators

```sql decision_values
select dataset_label, field_label, unit, value
from platform.dataset_values
where role = 'model_output' and value is not null
  and (dataset like '%summary%' or dataset like '%decision%')
order by dataset_label, field_label
```

{#if decision_values.length > 0}

<DataTable data={decision_values} rows=25>
  <Column id=dataset_label title="Output" />
  <Column id=field_label title="Indicator" />
  <Column id=value fmt="#,##0.0" />
  <Column id=unit />
</DataTable>

{:else}

No summary outputs have been computed yet in this organization.

{/if}

## Where values come from

```sql sources
select coalesce(nullif(current_source, ''), 'model result') as source, count(*) as datasets
from platform.datasets
group by 1
order by 2 desc
```

<BarChart data={sources} x=source y=datasets swapXY=true title="Datasets by current source" />

Pages: [Ripple effects](/ripple-effects) · [Master plan](/master-plan) · [Scenarios](/scenarios) · [Shared outputs](/shared-outputs)
