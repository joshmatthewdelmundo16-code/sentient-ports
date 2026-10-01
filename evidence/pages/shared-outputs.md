---
title: Shared outputs
---

Only fields that are approved, active and not expired appear here — the platform's
approved-output boundary decides, not this report. Revoked or expired approvals, and approvals
held by inactive participants, are excluded before the data ever reaches Evidence.

```sql exposed
select participant, dataset, field, value, value_source, purpose, approved_at, expires_at
from platform.exposed_outputs
order by participant, dataset, field
```

{#if exposed.length > 0}

<BigValue data={[{n: exposed.length}]} value=n title="Fields currently shared" />

<DataTable data={exposed} rows=25 groupBy=participant>
  <Column id=participant />
  <Column id=dataset />
  <Column id=field />
  <Column id=value fmt="#,##0.##" />
  <Column id=value_source title="Value from" />
  <Column id=purpose />
  <Column id=expires_at title="Expires" />
</DataTable>

{:else}

Nothing is shared from this organization at the moment.

{/if}
