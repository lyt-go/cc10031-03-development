/** 归档回查纯前端数据层：可回查范围视图、归档看板、两段式查询、校验清单与重算续算。 */
import { request } from '@/api/client'

export type RecallWindow = {
  窗口编号: string
  窗口序号: number
  确认值: number
  有效值: number
  保存位置: string
  冷档校验: string
  已归档: boolean
  回查状态: string
  待核修订: string
  已验收修订: string
  关联追踪: Record<string, string[]>
}

export type Revision = {
  revision_id: string
  window_id: string
  value: number
  state: string
  migrated_to_cold: boolean
  trace: string
}

export type CopyEntry = {
  copy_id: string
  window_id: string
  location: string
  state: string
  value: number
}

export type HotZone = { 用量: number; 上限: number; 接近轮换上限: boolean }

export type CoverageResult = {
  时间分区: string
  热区水位: HotZone
  windows: RecallWindow[]
  pending_revisions: Revision[]
}

export type VerifyRecord = {
  窗口编号: string
  copy_id: string
  ok: boolean
  note: string
  失败副本?: string
}

export type DashboardResult = {
  windows: (RecallWindow & { 副本: CopyEntry[] })[]
  pending_copies: CopyEntry[]
  pending_revisions: Revision[]
  热区水位: HotZone
  verify_records: VerifyRecord[]
}

export type IndexEntry = {
  索引编号: string
  时间分区: string
  窗口编号: string
  窗口序号: number
  点区间: string
  存储位置: string
  有效值: number
  已归档: boolean
}

export type QueryEntry = {
  query_id: string
  partition: string
  state: string
  delivered: boolean
  message?: string
}

export type QueryReadResult = {
  query_id: string
  state: string
  coverage: CoverageResult
  index: IndexEntry[]
  message: string
}

export type Checklist = {
  checklist_id: string
  partition: string
  round: number
  items: { 窗口编号: string; copy_id: string; location: string; value: number }[]
  excluded: { 窗口编号: string; 原因: string }[]
}

export type RecomputeResult = {
  窗口编号: string
  adopted_value: number
  location: string
  archived: boolean
  resumed_from: string | null
  archive_interrupted: boolean
  pending_windows: string[]
  关联追踪: Record<string, string[]>
  message?: string
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.detail ?? '读取失败')
  return payload as T
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.detail ?? '操作失败')
  return payload as T
}

export const loadCoverage = (partition: string) =>
  getJson<CoverageResult>(`/api/archive_recall/coverage?partition=${encodeURIComponent(partition)}`)

export const loadDashboard = () => getJson<DashboardResult>('/api/archive_recall/dashboard')

export const prepareQuery = (partition: string) =>
  postJson<QueryEntry>('/api/archive_recall/queries', { partition })

export const readQuery = (queryId: string) =>
  postJson<QueryReadResult>(`/api/archive_recall/queries/${encodeURIComponent(queryId)}/read`, {})

export const acceptRevision = (revisionId: string) =>
  postJson<{ revision: Revision; message: string }>(
    `/api/archive_recall/revisions/${encodeURIComponent(revisionId)}/accept`, {})

export const generateChecklist = (partition: string) =>
  postJson<Checklist>('/api/archive_recall/checklists', { partition })

export const verifyCopy = (windowId: string, copyId: string, ok: boolean, note = '') =>
  postJson<{ record: VerifyRecord; cold_verified: boolean }>(
    '/api/archive_recall/verify', { window_id: windowId, copy_id: copyId, ok, note })

export const recomputeWindow = (opts: {
  partition: string
  windowId?: string
  indexHandle?: string
  resume?: boolean
}) =>
  postJson<RecomputeResult>('/api/archive_recall/recompute', {
    partition: opts.partition,
    window_id: opts.windowId ?? null,
    index_handle: opts.indexHandle ?? null,
    resume: opts.resume ?? false,
  })

export const interruptArchive = () =>
  postJson<{ message: string }>('/api/archive_recall/interrupt', {})

export const locateIndex = (partition: string) =>
  getJson<{ partition: string; coverage_count: number; entries: IndexEntry[] }>(
    `/api/series_index/locate?partition=${encodeURIComponent(partition)}`)
