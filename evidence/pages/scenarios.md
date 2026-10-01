---
title: Scenarios against the baseline
---

Scenario runs are read-only in the platform: they never change today's values and are never
shared implicitly. Each row compares one indicator between the baseline run and the scenario run.

```sql scenarios
select distinct scenario from platform.scenario_comparison order by scenario
```

{#if scenarios.length > 0}

<Dropdown data={scenarios} name=scenario value=scenario title="Scenario" />

```sql comparison
select dataset, field, unit, baseline, scenario_value, absolute_delta, relative_delta, direction
from platform.scenario_comparison
where scenario = '${inputs.scenario.value}'
order by abs(coalesce(relative_delta, 0)) desc
```

```sql biggest
select field || ' (' || dataset || ')' as indicator, relative_delta
from platform.scenario_comparison
where scenario = '${inputs.scenario.value}' and relative_delta is not null and relative_delta <> 0
order by abs(relative_delta) desc
limit 12
```

<BarChart data={biggest} x=indicator y=relative_delta swapXY=true yFmt="pct0" title="Largest relative changes" />

<DataTable data={comparison} rows=25>
  <Column id=dataset />
  <Column id=field />
  <Column id=baseline fmt="#,##0.##" />
  <Column id=scenario_value title="Scenario" fmt="#,##0.##" />
  <Column id=relative_delta title="Change" fmt="pct1" contentType=delta />
  <Column id=unit />
</DataTable>

{:else}

No executed scenarios in this organization yet.

{/if}
