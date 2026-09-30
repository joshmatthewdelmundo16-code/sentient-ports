/**
 * The baseline and scenario the user is looking at. Defaults come from the server's
 * workspace summary (derived from records); the user's choice is remembered per scope.
 */
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import { useWorkspace } from '../api/queries'
import type { BaselineView, ScenarioView, Workspace } from '../api/types'
import { useScope } from './scope'

interface SelectionValue {
  workspace: Workspace | undefined
  loading: boolean
  error: unknown
  refetch: () => void
  baseline: BaselineView | null
  scenario: ScenarioView | null
  scenariosForBaseline: ScenarioView[]
  selectBaseline: (id: string) => void
  selectScenario: (id: string) => void
}

const SelectionContext = createContext<SelectionValue | null>(null)

function storageKey(scopeId: string | undefined) {
  return `platform.selection.${scopeId ?? 'local'}`
}

function load(scopeId: string | undefined): { b?: string; s?: string } {
  try {
    return JSON.parse(window.sessionStorage.getItem(storageKey(scopeId)) ?? '{}') as { b?: string; s?: string }
  } catch {
    return {}
  }
}

export function SelectionProvider({ children }: { children: ReactNode }) {
  const { scope } = useScope()
  const ws = useWorkspace()
  const [picked, setPicked] = useState<Record<string, { b?: string; s?: string }>>({})
  const scopeId = scope?.id
  const key = scopeId ?? 'local'
  const current = picked[key] ?? load(scopeId)

  const value = useMemo<SelectionValue>(() => {
    const data = ws.data
    const baselines = data?.baselines ?? []
    const scenarios = data?.scenarios ?? []
    const baseline =
      baselines.find((b) => b.id === current.b) ??
      baselines.find((b) => b.id === data?.default.baseline_id) ??
      baselines[0] ??
      null
    const forBaseline = scenarios.filter((s) => s.baseline_id === baseline?.id)
    const scenario =
      forBaseline.find((s) => s.id === current.s) ??
      forBaseline.find((s) => s.id === data?.default.scenario_id) ??
      forBaseline[0] ??
      null
    const save = (next: { b?: string; s?: string }) => {
      setPicked((p) => ({ ...p, [key]: next }))
      try { window.sessionStorage.setItem(storageKey(scopeId), JSON.stringify(next)) } catch { /* ignore */ }
    }
    return {
      workspace: data,
      loading: ws.isLoading,
      error: ws.error,
      refetch: () => void ws.refetch(),
      baseline,
      scenario,
      scenariosForBaseline: forBaseline,
      selectBaseline: (id) => save({ b: id }),
      selectScenario: (id) => save({ b: baseline?.id, s: id }),
    }
  }, [ws, current.b, current.s, key, scopeId])

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>
}

export function useSelection(): SelectionValue {
  const v = useContext(SelectionContext)
  if (!v) throw new Error('useSelection must be used inside SelectionProvider')
  return v
}
