import type { ReactNode } from 'react'
import { ActivityPage } from '../features/activity/ActivityPage'
import { DecisionPage } from '../features/decision/DecisionPage'
import { ExcelPage } from '../features/excel/ExcelPage'
import { ExecutionPage } from '../features/execution/ExecutionPage'
import { GovernancePage } from '../features/governance/GovernancePage'
import { ImpactPage } from '../features/impact/ImpactPage'
import { SourcesPage } from '../features/provenance/SourcesPage'
import { ScenarioPage } from '../features/scenario/ScenarioPage'
import { StartPage } from '../features/start/StartPage'

/** Every page the product serves. The navigation only lists paths that appear here. */
export const ROUTES: { path: string; element: ReactNode }[] = [
  { path: '/', element: <StartPage /> },
  { path: '/decision', element: <DecisionPage /> },
  { path: '/scenarios', element: <ScenarioPage /> },
  { path: '/excel', element: <ExcelPage /> },
  { path: '/impact', element: <ImpactPage /> },
  { path: '/sources', element: <SourcesPage /> },
  { path: '/activity', element: <ActivityPage /> },
  { path: '/execution', element: <ExecutionPage /> },
  { path: '/governance', element: <GovernancePage /> },
]

export const ROUTE_PATHS = new Set(ROUTES.map((r) => r.path))
