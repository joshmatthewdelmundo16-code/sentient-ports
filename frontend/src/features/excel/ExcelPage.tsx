import { useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import {
  scoped,
  scopedGet,
  useCatalog,
  useCommitWorkbook,
  useMappingPreview,
  useMappings,
  useUploadImpact,
  useValidateWorkbook,
} from '../../api/queries'
import type { IngestionOut, WorkbookPreview } from '../../api/types'
import { useScope } from '../../app/scope'
import { formatDateTime, formatDelta, formatPct, formatTransition, formatValue, shortId, statusLabel } from '../../shared/format'
import { Badge, Callout, Card, ErrorState, Loading, PageHeader, StatusBadge, TechnicalDetails } from '../../shared/ui'

type StepState = 'done' | 'current' | 'failed' | 'todo'

function Step({ n, title, state, children }: { n: number; title: string; state: StepState; children?: ReactNode }) {
  return (
    <li className={`step ${state === 'todo' ? '' : state}`}>
      <span className="step-marker" aria-hidden="true">{state === 'done' ? '✓' : state === 'failed' ? '!' : n}</span>
      <div className="step-body">
        <div className="step-title">{title}</div>
        {children ? <div className="stack-sm" style={{ marginTop: 6 }}>{children}</div> : null}
      </div>
    </li>
  )
}

interface IngestionRow {
  id: string
  source_name: string
  status: string
  created_at: string
  content_sha256: string | null
}

export function ExcelPage() {
  const mappings = useMappings()
  const catalog = useCatalog()
  const { can } = useScope()
  const [mappingKey, setMappingKey] = useState<string | null>(null)
  const key = mappingKey ?? mappings.data?.[0]?.key ?? null
  const preview = useMappingPreview(key)
  const validate = useValidateWorkbook()
  const commit = useCommitWorkbook()
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [committed, setCommitted] = useState<IngestionOut | null>(null)
  const impact = useUploadImpact(committed?.ingestion_id)
  const inputRef = useRef<HTMLInputElement>(null)
  const recent = useQuery({ queryKey: scoped('ingestions'), queryFn: scopedGet<IngestionRow[]>('/api/ingestions?limit=6') })

  const byName = useMemo(() => new Map((catalog.data ?? []).map((d) => [d.name, d])), [catalog.data])
  const label = (dataset: string, field: string) => {
    const d = byName.get(dataset)
    return { field: d?.fields.find((f) => f.name === field)?.label ?? field, dataset: d?.label ?? dataset }
  }

  const choose = (f: File | null) => {
    setFile(f)
    setCommitted(null)
    validate.reset()
    commit.reset()
    if (f && key) validate.mutate({ mapping: key, file: f })
  }
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files?.[0]
    if (f) choose(f)
  }

  const result: WorkbookPreview | undefined = validate.data
  const rejected = committed?.status === 'rejected'
  const canWrite = can('analyst')

  const st = (done: boolean, failed = false, current = false): StepState =>
    failed ? 'failed' : done ? 'done' : current ? 'current' : 'todo'

  const imp = impact.data
  const changedMetrics = imp?.comparison?.metrics.filter((m) => m.changed) ?? []

  return (
    <div className="stack-lg">
      <PageHeader
        title="Assumptions & Excel"
        description="Bring assumptions in from a workbook. You see exactly what would change before anything is written, and every committed value keeps a link back to the file it came from."
      />
      <Callout tone="info" title="Upload-based ingestion — not synchronisation.">
        The platform reads a workbook only when you upload it here. It does not watch, schedule or sync files. Only cached cell values in the mapped cells are read: formulas are not evaluated, macros never run and external links are not followed. Files must be .xlsx and at most 5 MB.
      </Callout>

      <Card>
        <ol className="stepper" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          <Step n={1} title="Upload workbook" state={st(Boolean(file), false, !file)}>
            <div className="row" style={{ alignItems: 'flex-end' }}>
              <div className="field" style={{ minWidth: 260 }}>
                <label htmlFor="mapping">Mapping</label>
                <select id="mapping" className="input" value={key ?? ''} onChange={(e) => { setMappingKey(e.target.value); choose(null) }}>
                  {(mappings.data ?? []).map((m) => <option key={m.key} value={m.key}>{m.description}</option>)}
                </select>
              </div>
              {key ? <a className="btn" href={`/api/ingestions/template?mapping=${encodeURIComponent(key)}`}>Download template</a> : null}
            </div>
            <div className={`dropzone ${dragging ? 'active' : ''}`} role="button" tabIndex={0}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click() }}
              onDragOver={(e) => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
              <strong>{file ? file.name : 'Drop an .xlsx workbook here, or select one'}</strong>
              <div className="small muted">{file ? `${(file.size / 1024).toFixed(1)} KB · checked in a dry run, nothing written yet` : 'The template is pre-filled with the values the platform holds now.'}</div>
              <input ref={inputRef} type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                className="visually-hidden" data-testid="workbook-input" onChange={(e) => choose(e.target.files?.[0] ?? null)} />
            </div>
          </Step>

          <Step n={2} title="Inspect" state={st(Boolean(result), Boolean(validate.error), Boolean(file) && !result)}>
            {validate.isPending ? <Loading lines={2} /> : null}
            {validate.error ? <ErrorState error={validate.error} /> : null}
            {result ? (
              <div className="small">
                Read <strong>{result.fields.length}</strong> mapped cell{result.fields.length === 1 ? '' : 's'} from <strong>{result.source_name}</strong>.
                <TechnicalDetails items={{ 'SHA-256': result.content_sha256, Mapping: result.mapping_key }} />
                {result.notes.map((n) => <div key={n} className="muted">{n}</div>)}
              </div>
            ) : null}
          </Step>

          <Step n={3} title="Mapping" state={st(Boolean(preview.data))}>
            {preview.isLoading ? <Loading lines={2} /> : preview.data ? (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Cell</th><th>Becomes</th><th className="num">Platform holds now</th></tr></thead>
                  <tbody>
                    {preview.data.fields.map((f) => {
                      const l = label(f.dataset, f.field)
                      return (
                        <tr key={`${f.worksheet}!${f.cell}`}>
                          <td className="mono">{f.worksheet}!{f.cell}</td>
                          <td><strong>{l.field}</strong> <span className="muted small">· {l.dataset}</span></td>
                          <td className="num">{formatValue(f.current_value, f.unit)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Step>

          <Step n={4} title="Validate" state={st(Boolean(result?.valid), Boolean(result && !result.valid))}>
            {result ? (
              <>
                {result.valid ? (
                  <Callout tone="success">
                    Valid against the data contract. {result.would_change ? `${result.changed_fields.length} value${result.changed_fields.length === 1 ? '' : 's'} would change.` : 'Nothing would change — the workbook matches the current data.'}
                  </Callout>
                ) : (
                  <Callout tone="danger" title="Not valid.">Nothing will be written. {result.errors.join(' ')}</Callout>
                )}
                <div className="table-wrap">
                  <table className="table" data-testid="validation-table">
                    <thead><tr><th>Field</th><th className="num">Current → workbook</th><th>Check</th></tr></thead>
                    <tbody>
                      {result.fields.map((f) => (
                        <tr key={`${f.worksheet}!${f.cell}`} className={f.changed ? 'changed' : 'dim'}>
                          <td>{label(f.dataset, f.field).field}</td>
                          <td className="num">{f.changed ? formatTransition(f.current_value, f.new_value, f.unit) : `${formatValue(f.new_value, f.unit)} (same)`}</td>
                          <td>{f.valid ? <Badge tone="success">Valid</Badge> : <Badge tone="danger" title={f.message ?? ''}>{f.message ?? 'Invalid'}</Badge>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : <span className="small muted">Runs automatically as a dry run when you choose a file. A dry run writes nothing.</span>}
          </Step>

          <Step n={5} title="Commit" state={st(Boolean(committed && !rejected), rejected, Boolean(result?.valid) && !committed)}>
            {!canWrite ? <Callout tone="warning">Your role in this scope cannot commit data.</Callout> : null}
            <div className="row">
              <button className="btn btn-primary" data-testid="commit-workbook"
                disabled={!canWrite || !file || !key || !result?.valid || commit.isPending || Boolean(committed)}
                onClick={() => file && key && commit.mutate({ mapping: key, file }, { onSuccess: setCommitted })}>
                {commit.isPending ? 'Committing…' : 'Commit workbook'}
              </button>
              {committed ? <StatusBadge status={committed.status} /> : null}
            </div>
            {commit.error ? <ErrorState error={commit.error} /> : null}
            {rejected ? <Callout tone="danger" title="Rejected.">{committed?.error}</Callout> : null}
            {committed?.status === 'unchanged' ? <Callout tone="neutral">Recorded as an unchanged upload. No data changed, so nothing downstream ran.</Callout> : null}
          </Step>

          <Step n={6} title="Change detection" state={st(Boolean(imp?.changes.length))}>
            {imp ? (imp.changes.length ? (
              <div className="stack-sm">
                {imp.changes.map((c) => (
                  <div key={`${c.dataset_id}:${c.field}`} className="small">
                    <strong>{c.field_label}</strong> <span className="muted">· {c.dataset_label}</span>: <span className="num">{formatTransition(c.from, c.to, c.unit)}</span>
                    <TechnicalDetails items={{ 'Change event': c.change_event_id, 'Old hash': c.old_hash, 'New hash': c.new_hash }} />
                  </div>
                ))}
              </div>
            ) : <span className="small muted">No field changed.</span>) : <span className="small muted">After commit, the new value is hashed and compared with the current one. Only a real difference creates a change event.</span>}
          </Step>

          <Step n={7} title="Impact" state={st(Boolean(imp?.models_run.length))}>
            {imp?.models_run.length ? (
              <div className="pill-list">
                {imp.models_run.map((m) => <Badge key={m.version_id} tone="info">{m.model_name}</Badge>)}
              </div>
            ) : <span className="small muted">Every model that depends on a changed dataset is identified from the dependency graph.</span>}
          </Step>

          <Step n={8} title="Execution" state={st(imp?.run?.status === 'succeeded', imp?.run?.status === 'failed')}>
            {imp?.run ? (
              <div className="row small">
                <StatusBadge status={imp.run.status} /> Propagation run · {formatDateTime(imp.run.finished_at)}
                <Link to={`/execution?run=${imp.run.id}`}>Open run</Link>
              </div>
            ) : <span className="small muted">The affected models run once, in dependency order, as a single recorded run.</span>}
          </Step>

          <Step n={9} title="Compare" state={st(Boolean(imp?.comparison))}>
            {imp?.comparison ? (changedMetrics.length ? (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Result</th><th className="num">Before → after</th><th className="num">Change</th></tr></thead>
                  <tbody>
                    {changedMetrics.map((m) => (
                      <tr key={`${m.dataset_id}:${m.field}`}>
                        <td>{m.field_label} <span className="muted small">· {m.dataset_label}</span></td>
                        <td className="num">{formatTransition(m.baseline, m.scenario, m.unit)}</td>
                        <td className="num">{m.kind === 'numeric' ? `${formatDelta(m.absolute_delta, m.unit)} (${formatPct(m.relative_delta)})` : 'Changed'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <span className="small muted">No result changed.</span>) : <span className="small muted">Results are compared with the previous authoritative run.</span>}
          </Step>

          <Step n={10} title="Provenance" state={st(Boolean(imp))}>
            {imp ? (
              <div className="small">
                Every changed value now records this upload as its source. <Link to="/sources">Sources & provenance</Link>
                <TechnicalDetails items={{
                  Ingestion: imp.ingestion.id, File: imp.ingestion.file_name, 'SHA-256': imp.ingestion.content_sha256,
                  Mapping: imp.ingestion.mapping_key, 'Propagation run': imp.run?.id, 'Compared with run': imp.compared_with?.id,
                }} />
              </div>
            ) : <span className="small muted">The file name, its SHA-256 and the mapping are kept with every value it changed.</span>}
          </Step>
        </ol>
      </Card>

      <Card title="Recent uploads">
        {recent.data && recent.data.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>File</th><th>Status</th><th>When</th><th>Content</th></tr></thead>
              <tbody>
                {recent.data.map((r) => (
                  <tr key={r.id}>
                    <td>{r.source_name}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td>{formatDateTime(r.created_at)}</td>
                    <td className="mono tiny">{r.content_sha256 ? shortId(r.content_sha256) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="muted small">No workbook has been uploaded in this scope yet.</p>}
        <p className="tiny muted" style={{ marginTop: 8 }}>{statusLabel('ingested')} means values were written. {statusLabel('unchanged')} means the workbook matched the current data.</p>
      </Card>
    </div>
  )
}
