import { Link } from 'react-router'
import { ProvenanceSteps, type ProvStep } from '../../components/data/ProvenanceSteps'
import { buttonClass, Button } from '../../components/ui/Button'
import { Drawer } from '../../components/ui/Drawer'
import { Icon } from '../../components/ui/Icon'

export interface ProvenanceView {
  title: string
  subtitle: string
  steps: ProvStep[]
}

/** Lineage drawer. Used for the whole result (header button) and for a single node of the ripple. */
export function ProvenanceDrawer({ view, onClose }: { view: ProvenanceView | null; onClose: () => void }) {
  return (
    <Drawer
      open={view !== null}
      onClose={onClose}
      title={view?.title ?? 'Provenance'}
      subtitle={view?.subtitle}
      footer={
        <>
          <Button variant="outline" block onClick={onClose}>Close</Button>
          <Link className={buttonClass('primary', 'md', true)} to="/sources" onClick={onClose}>
            <Icon name="branch" size={14} /> Sources &amp; Provenance
          </Link>
        </>
      }
    >
      {view ? <ProvenanceSteps steps={view.steps} /> : null}
      <p className="muted" style={{ fontSize: 11, marginTop: 14 }}>
        Every result traces to a model version and a governed data source. Scenario values are applied in memory for the scenario run only; they are never written to these datasets.
      </p>
    </Drawer>
  )
}
