/** Response shapes of the FastAPI endpoints the product uses. Kept in one place so a
 *  backend contract change fails the TypeScript build instead of a screen at runtime. */

export type Scalar = number | string | boolean | null

export interface RunBrief {
  id: string
  status: string
  executor: string
  finished_at: string | null
  started_at: string | null
}

export interface OverrideView {
  id: string
  dataset_id: string
  dataset_label: string
  field: string
  field_label: string
  unit: string | null
  value: Scalar
  baseline_value: Scalar
  baseline_value_known: boolean
}

export interface BaselineView {
  id: string
  name: string
  description: string | null
  status: string
  target_version_id: string | null
  target_model: string | null
  run: RunBrief | null
  created_at: string | null
  updated_at: string | null
}

export interface ScenarioView {
  id: string
  name: string
  description: string | null
  status: string
  baseline_id: string
  baseline_name: string | null
  run: RunBrief | null
  overrides: OverrideView[]
  created_at: string | null
  updated_at: string | null
}

export interface FieldMeta {
  name: string
  label: string
  unit: string | null
  type: string | null
  nullable: boolean
  min: number | null
  max: number | null
  description: string | null
}

export interface ModelRef {
  version_id: string
  model_name: string
}

export interface CurrentSource {
  change_event_id: string
  source_type: string | null
  source_ref: string | null
  triggered_by: string | null
  produced_by_run_id: string | null
  at: string | null
}

export interface CatalogDataset {
  id: string
  name: string
  label: string
  description: string | null
  role: 'source' | 'model_output'
  fields: FieldMeta[]
  contract: { id: string; semver: string } | null
  value: Record<string, Scalar> | null
  value_hash: string | null
  updated_at: string | null
  owner_participant_id: string | null
  produced_by: ModelRef[]
  consumed_by: ModelRef[]
  current_source: CurrentSource | null
}

export interface Workspace {
  baselines: BaselineView[]
  scenarios: ScenarioView[]
  assumptions: CatalogDataset[]
  default: { baseline_id: string | null; scenario_id: string | null }
  counts: { models: number; datasets: number; runs: number }
}

export interface Metric {
  dataset_id: string
  field: string
  kind: 'numeric' | 'boolean' | 'other'
  baseline: Scalar
  scenario: Scalar
  unit: string | null
  absolute_delta: number | null
  relative_delta: number | null
  baseline_zero: boolean
  direction: 'increase' | 'decrease' | 'none' | null
  changed: boolean
  dataset_label: string
  field_label: string
  terminal: boolean
  field_order: number
}

export interface Comparison {
  base_run_id: string
  target_run_id: string
  metrics: Metric[]
  changed_count: number
}

export interface FieldRef {
  dataset_id: string
  field: string
  field_label: string
  dataset_label: string
}

export interface PathStep {
  version_id: string
  model_name: string
  reads_changed: FieldRef[]
  outputs_changed: FieldRef[]
  outputs_unchanged: FieldRef[]
}

export interface Explanation {
  scenario: { id: string; name: string; run_id: string }
  baseline: { id: string; name: string; run_id: string }
  changes: {
    dataset_id: string
    dataset_label: string
    field: string
    field_label: string
    unit: string | null
    baseline_value: Scalar
    baseline_value_known: boolean
    scenario_value: Scalar
  }[]
  path: PathStep[]
  unaffected: { version_id: string; model_name: string; reason: string }[]
  comparison: Comparison
}

export interface MapModel {
  version_id: string
  model_id: string
  name: string
  semver: string
  is_active: boolean
  status: string
  adapter_type: string | null
  owner: string
  purpose: string | null
  formula: string | null
  domain: string | null
  calibration: string | null
}

export interface FederationMap {
  models: MapModel[]
  datasets: { id: string; name: string | null; label: string; role: 'source' | 'model_output' }[]
  reads: { version_id: string; dataset_id: string; fields: string[] | null }[]
  writes: { version_id: string; dataset_id: string; fields: string[] | null }[]
}

export interface Headline {
  dataset_id: string
  field: string
  label: string
  unit: string | null
  from?: Scalar
  to?: Scalar
  value?: Scalar
  relative_delta?: number | null
}

export interface ActivityItem {
  id: string
  kind: 'baseline_run' | 'scenario_run' | 'dataset_change' | 'upload' | 'model_run' | 'plan_run' | 'governance'
  title: string
  subject: string
  context: string | null
  status: string
  occurred_at: string | null
  headline: Headline | null
  effect?: Headline | null
  error?: string | null
  changed_fields?: { field: string; label: string }[]
  via?: { source_type: string; source_ref: string | null; kind?: string; file_name?: string; content_sha256?: string; mapping_key?: string }
  links: Record<string, string | null | undefined>
  technical: Record<string, string | null | undefined>
}

export interface ExecutionRun {
  id: string
  run_kind: string
  executor: string
  status: string
  target_version_id: string | null
  trigger_type: string | null
  triggered_by: string | null
  error_message: string | null
  subgraph_json: string | null
  scenario_id: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface ExecutionStep {
  id: string
  run_id: string
  model_version_id: string | null
  step_order: number
  status: string
  input_snapshot: string | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ExecutionDetail {
  run: ExecutionRun
  steps: ExecutionStep[]
}

export interface ResultRow {
  id: string
  run_id: string
  step_id: string
  dataset_id: string
  model_version_id: string | null
  scenario_id: string | null
  value_json: string
  value_hash: string | null
  created_at: string
}

export interface LineageEdge {
  id: string
  run_id: string
  step_id: string
  source_result_id: string | null
  target_result_id: string | null
  source_dataset_id: string | null
  target_dataset_id: string | null
  source_change_event_id: string | null
  created_at: string
}

export interface Mapping {
  key: string
  description: string
  cells: { worksheet: string; cell: string; target_dataset: string; target_field: string; expected_type: string }[]
}

export interface MappingPreview {
  key: string
  description: string
  datasets: string[]
  fields: { worksheet: string; cell: string; dataset: string; field: string; unit: string | null; expected_type: string; current_value: Scalar }[]
  notes: string[]
}

export interface WorkbookPreview {
  mapping_key: string
  mapping_description: string
  source_name: string
  content_sha256: string
  fields: {
    worksheet: string
    cell: string
    dataset: string
    field: string
    unit: string | null
    expected_type: string
    current_value: Scalar
    new_value: Scalar
    changed: boolean
    valid: boolean
    message: string | null
  }[]
  valid: boolean
  changed_fields: string[]
  would_change: boolean
  datasets: string[]
  errors: string[]
  notes: string[]
}

export interface IngestionOut {
  ingestion_id: string
  status: 'ingested' | 'unchanged' | 'rejected'
  content_sha256: string | null
  mapping_key: string
  source_name: string
  changed: boolean
  dataset_ids: string[]
  change_event_id: string | null
  graph_run_id: string | null
  error: string | null
}

export interface UploadImpact {
  ingestion: {
    id: string
    file_name: string
    status: string
    mapping_key: string
    content_sha256: string | null
    error: string | null
    created_at: string | null
    dataset_ids: string[]
  }
  changes: {
    dataset_id: string
    dataset_label: string
    field: string
    field_label: string
    unit: string | null
    from: Scalar
    to: Scalar
    change_event_id: string
    old_hash: string | null
    new_hash: string | null
  }[]
  run: RunBrief | null
  models_run: ModelRef[]
  comparison: Comparison | null
  compared_with?: RunBrief
}

export interface BuildInfo {
  name: string
  version: string
  phase: string
  environment: string
  database: string
  demo_seed_enabled: boolean
  public_base_url: string | null
  missing_capabilities: string[]
  frontend?: { built: boolean; build_id?: string; built_at?: string; api_version?: string }
  auth_mode?: string
}

export interface GovernanceSummary {
  participants: number
  active_participants: number
  approvals: number
  active_approvals: number
  effective_approvals: number
  revoked_approvals: number
}

export interface Participant {
  id: string
  participant_key: string
  name: string
  description: string | null
  status: string
  metadata: Record<string, unknown> | null
  organization_id?: string | null
  created_at: string
}

export interface Approval {
  id: string
  participant_id: string
  dataset_id: string
  field_name: string
  purpose: string | null
  status: string
  approved_at: string | null
  expires_at: string | null
  revoked_at: string | null
  metadata: Record<string, unknown> | null
  audience_organization_id?: string | null
  collaboration_case_id?: string | null
  source_run_id?: string | null
}

export interface ExposedField {
  participant_id: string
  participant_key: string
  dataset_id: string
  dataset_name: string
  field_name: string
  purpose: string | null
  value: Scalar
  value_present: boolean
  approved_at: string | null
  expires_at: string | null
}
