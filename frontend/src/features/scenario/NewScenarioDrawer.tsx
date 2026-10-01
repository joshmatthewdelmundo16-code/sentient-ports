import { useState, type FormEvent } from 'react'
import { useCreateScenario } from '../../api/queries'
import { Button, LoadingButton } from '../../components/ui/Button'
import { Drawer } from '../../components/ui/Drawer'
import { useToast } from '../../components/ui/Toast'
import { Callout, Field } from '../../shared/ui'

const FORM_ID = 'new-scenario-form'

/** Create a scenario from the selected baseline (reference "New Scenario" drawer). */
export function NewScenarioDrawer({ open, onClose, baselineId, baselineName, onCreated }: {
  open: boolean
  onClose: () => void
  baselineId: string
  baselineName: string
  onCreated: (id: string) => void
}) {
  const create = useCreateScenario()
  const toast = useToast()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const created = name.trim()
    if (!created || create.isPending) return
    create.mutate(
      { baseline_id: baselineId, name: created, description: description.trim() || undefined },
      {
        onSuccess: (s) => {
          setName('')
          setDescription('')
          onCreated(s.id)
          onClose()
          toast.success('Scenario Created', created)
        },
      },
    )
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="New Scenario"
      subtitle={`Derived From The Baseline “${baselineName}”`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <LoadingButton variant="primary" icon="plus" type="submit" form={FORM_ID} loading={create.isPending} loadingLabel="Creating Scenario..." disabled={!name.trim()} style={{ flex: 1 }}>
            Create Scenario
          </LoadingButton>
        </>
      }
    >
      <form id={FORM_ID} onSubmit={submit} className="stack" style={{ gap: 15 }}>
        <Field label="Scenario Name">
          {(id) => <input id={id} className="input" data-testid="new-scenario-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Two new berths" autoComplete="off" />}
        </Field>
        <Field label="Description (Optional)" hint="Shown in the scenario list. Assumptions are set after the scenario exists.">
          {(id) => <input id={id} className="input" data-testid="new-scenario-desc" value={description} onChange={(e) => setDescription(e.target.value)} autoComplete="off" />}
        </Field>
        {create.error ? <Callout tone="danger">{(create.error as Error).message}</Callout> : null}
      </form>
    </Drawer>
  )
}
