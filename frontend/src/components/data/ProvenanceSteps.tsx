export interface ProvStep {
  key: string
  value: string
  meta?: string
}

/** Numbered lineage trail (reference `.prov`): result → models → inputs → sources → runs. */
export function ProvenanceSteps({ steps }: { steps: ProvStep[] }) {
  return (
    <div className="prov">
      {steps.map((s, i) => (
        <div key={s.key} className="prov-step">
          <div className="ps-dot">{i + 1}</div>
          <div>
            <div className="ps-k">{s.key}</div>
            <div className="ps-v">{s.value}</div>
            {s.meta ? <div className="ps-meta">{s.meta}</div> : null}
          </div>
        </div>
      ))}
    </div>
  )
}
