/**
 * TanStack Query hooks — one per API resource. Query keys include the active scope, so
 * switching organization never shows one scope's cached data inside another.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, getActiveScope } from './client'
import type {
  ActivityItem,
  Approval,
  BuildInfo,
  CatalogDataset,
  Comparison,
  ExecutionDetail,
  ExecutionRun,
  Explanation,
  ExposedField,
  FederationMap,
  GovernanceSummary,
  IngestionOut,
  LineageEdge,
  Mapping,
  MappingPreview,
  Participant,
  ResultRow,
  UploadImpact,
  WorkbookPreview,
  Workspace,
} from './types'

export const scoped = (...parts: unknown[]) => ['scope', getActiveScope() ?? 'none', ...parts]

export function useBuildInfo() {
  return useQuery({ queryKey: ['build-info'], queryFn: () => api<BuildInfo>('/api/build-info'), staleTime: 60_000 })
}

export function useWorkspace() {
  return useQuery({ queryKey: scoped('workspace'), queryFn: () => api<Workspace>('/api/workspace') })
}

export function useCatalog() {
  return useQuery({ queryKey: scoped('catalog'), queryFn: () => api<CatalogDataset[]>('/api/catalog') })
}

export function useFederationMap() {
  return useQuery({ queryKey: scoped('federation-map'), queryFn: () => api<FederationMap>('/api/federation/map') })
}

export function useActivity(limit = 40) {
  return useQuery({
    queryKey: scoped('activity', limit),
    queryFn: () => api<ActivityItem[]>(`/api/activity?limit=${limit}`),
  })
}

export function useExplanation(scenarioId: string | null | undefined) {
  return useQuery({
    queryKey: scoped('explanation', scenarioId),
    queryFn: () => api<Explanation>(`/api/scenarios/${scenarioId}/explanation`),
    enabled: Boolean(scenarioId),
    retry: false,
  })
}

export function useRunCompare(base: string | null | undefined, target: string | null | undefined) {
  return useQuery({
    queryKey: scoped('run-compare', base, target),
    queryFn: () => api<Comparison>(`/api/runs/compare?base=${base}&target=${target}`),
    enabled: Boolean(base && target),
  })
}

export function useExecutions(limit = 30) {
  return useQuery({
    queryKey: scoped('executions', limit),
    queryFn: () => api<ExecutionRun[]>(`/api/executions?limit=${limit}`),
  })
}

export function useExecution(runId: string | null | undefined) {
  return useQuery({
    queryKey: scoped('execution', runId),
    queryFn: () => api<ExecutionDetail>(`/api/executions/${runId}`),
    enabled: Boolean(runId),
  })
}

export function useRunResults(runId: string | null | undefined) {
  return useQuery({
    queryKey: scoped('run-results', runId),
    queryFn: () => api<ResultRow[]>(`/api/executions/${runId}/results`),
    enabled: Boolean(runId),
  })
}

export function useRunLineage(runId: string | null | undefined) {
  return useQuery({
    queryKey: scoped('run-lineage', runId),
    queryFn: () => api<LineageEdge[]>(`/api/executions/${runId}/lineage`),
    enabled: Boolean(runId),
  })
}

export function useMappings() {
  return useQuery({ queryKey: scoped('mappings'), queryFn: () => api<Mapping[]>('/api/ingestions/mappings') })
}

export function useMappingPreview(key: string | null | undefined) {
  return useQuery({
    queryKey: scoped('mapping-preview', key),
    queryFn: () => api<MappingPreview>(`/api/ingestions/mappings/${encodeURIComponent(key ?? '')}/preview`),
    enabled: Boolean(key),
  })
}

export function useUploadImpact(ingestionId: string | null | undefined) {
  return useQuery({
    queryKey: scoped('upload-impact', ingestionId),
    queryFn: () => api<UploadImpact>(`/api/ingestions/${ingestionId}/impact`),
    enabled: Boolean(ingestionId),
  })
}

function workbookForm(file: File): FormData {
  const form = new FormData()
  form.append('file', file, file.name)
  return form
}

export function useValidateWorkbook() {
  return useMutation({
    mutationFn: ({ mapping, file }: { mapping: string; file: File }) =>
      api<WorkbookPreview>(`/api/ingestions/excel/validate?mapping=${encodeURIComponent(mapping)}`, {
        form: workbookForm(file),
      }),
  })
}

/** Invalidate everything that can change when data changes in the current scope. */
export function useInvalidateScope() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: ['scope', getActiveScope() ?? 'none'] })
}

export function useCommitWorkbook() {
  const invalidate = useInvalidateScope()
  return useMutation({
    mutationFn: ({ mapping, file }: { mapping: string; file: File }) =>
      api<IngestionOut>(`/api/ingestions/excel?mapping=${encodeURIComponent(mapping)}`, {
        form: workbookForm(file),
      }),
    onSuccess: () => invalidate(),
  })
}

export function useSetOverrides() {
  const invalidate = useInvalidateScope()
  return useMutation({
    mutationFn: ({ scenarioId, overrides }: {
      scenarioId: string
      overrides: { dataset_id: string; field_name: string; value: number | string | boolean }[]
    }) => api(`/api/scenarios/${scenarioId}/overrides`, { method: 'PUT', json: { overrides } }),
    onSuccess: () => invalidate(),
  })
}

export function useRemoveOverride() {
  const invalidate = useInvalidateScope()
  return useMutation({
    mutationFn: ({ scenarioId, overrideId }: { scenarioId: string; overrideId: string }) =>
      api(`/api/scenarios/${scenarioId}/overrides/${overrideId}`, { method: 'DELETE' }),
    onSuccess: () => invalidate(),
  })
}

export function useRunScenario() {
  const invalidate = useInvalidateScope()
  return useMutation({
    mutationFn: (scenarioId: string) => api(`/api/scenarios/${scenarioId}/execute`, { method: 'POST' }),
    onSuccess: () => invalidate(),
  })
}

export function useRunBaseline() {
  const invalidate = useInvalidateScope()
  return useMutation({
    mutationFn: (baselineId: string) => api(`/api/baselines/${baselineId}/execute`, { method: 'POST' }),
    onSuccess: () => invalidate(),
  })
}

export function useCreateScenario() {
  const invalidate = useInvalidateScope()
  return useMutation({
    mutationFn: (body: { baseline_id: string; name: string; description?: string }) =>
      api<{ id: string }>('/api/scenarios', { json: body }),
    onSuccess: () => invalidate(),
  })
}

export function useGovernanceSummary() {
  return useQuery({ queryKey: scoped('gov-summary'), queryFn: () => api<GovernanceSummary>('/api/governance/summary') })
}

export function useParticipants() {
  return useQuery({ queryKey: scoped('participants'), queryFn: () => api<Participant[]>('/api/participants') })
}

export function useApprovals() {
  return useQuery({ queryKey: scoped('approvals'), queryFn: () => api<Approval[]>('/api/approved-outputs') })
}

export function useExposedOutputs() {
  return useQuery({ queryKey: scoped('exposed'), queryFn: () => api<ExposedField[]>('/api/exposed-outputs') })
}
