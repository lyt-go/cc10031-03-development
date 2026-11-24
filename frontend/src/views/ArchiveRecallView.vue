<template>
  <section class="page" data-module="archive_recall">
    <header class="page-head">
      <div>
        <h2>归档回查</h2>
        <p class="page-desc">
          按时间分区展示序列可回查范围：每个窗口在哪个保存位置可回查、哪些副本尚待核验、哪些修订仍待验收。
        </p>
      </div>
      <div class="page-actions">
        <label class="partition-picker">
          <span>时间分区</span>
          <input v-model="partition" data-testid="partition-input" @change="reload" />
        </label>
        <button class="btn" data-testid="reload" type="button" @click="reload">刷新看板</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">热区水位</span>
        <strong class="stat-value" data-testid="hot-usage">
          {{ hotZone ? `${hotZone.用量}/${hotZone.上限}` : '—' }}
        </strong>
        <span v-if="hotZone?.接近轮换上限" class="warn-text">接近轮换上限</span>
      </article>
      <article class="stat-card">
        <span class="stat-label">可回查窗口</span>
        <strong class="stat-value" data-testid="recallable-count">{{ coverage?.windows.length ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">尚待核验的副本</span>
        <strong class="stat-value" data-testid="pending-copy-count">{{ dashboard?.pending_copies.length ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待核修订</span>
        <strong class="stat-value" data-testid="pending-revision-count">{{ dashboard?.pending_revisions.length ?? 0 }}</strong>
      </article>
    </div>

    <h3 class="section-title">可回查范围视图</h3>
    <table class="data-table" data-testid="coverage-table">
      <thead>
        <tr>
          <th>窗口编号</th><th>窗口序号</th><th>确认值</th><th>有效值</th>
          <th>保存位置</th><th>冷档校验</th><th>回查状态</th><th>已归档</th><th>关联追踪</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="win in coverage?.windows ?? []" :key="win.窗口编号" :data-testid="`coverage-${win.窗口编号}`">
          <td>{{ win.窗口编号 }}</td>
          <td>{{ win.窗口序号 }}</td>
          <td>{{ win.确认值 }}</td>
          <td>{{ win.有效值 }}</td>
          <td>{{ win.保存位置 }}</td>
          <td>{{ win.冷档校验 }}</td>
          <td>{{ win.回查状态 }}</td>
          <td>{{ win.已归档 ? '已归档' : '未归档' }}</td>
          <td>
            <span v-for="(refs, value) in win.关联追踪" :key="value" class="trace-chip">
              {{ value }} → {{ refs.join('、') }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">归档看板</h3>
    <div class="board-grid">
      <table class="data-table" data-testid="dashboard-table">
        <thead>
          <tr><th>窗口编号</th><th>可回查位置</th><th>副本</th></tr>
        </thead>
        <tbody>
          <tr v-for="win in dashboard?.windows ?? []" :key="win.窗口编号">
            <td>{{ win.窗口编号 }}</td>
            <td>{{ win.保存位置 }}</td>
            <td>
              <span v-for="copy in win.副本" :key="copy.copy_id" class="trace-chip">
                {{ copy.copy_id }}（{{ copy.location }}·{{ copy.state }}·值 {{ copy.value }}）
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <aside class="side-panel">
        <h4>尚待核验的副本</h4>
        <p v-for="copy in dashboard?.pending_copies ?? []" :key="copy.copy_id" class="side-line">
          {{ copy.copy_id }}（{{ copy.window_id }} · {{ copy.location }}）
        </p>
        <p v-if="!dashboard?.pending_copies.length" class="side-line muted">无待核验副本</p>
        <h4>待核修订（单独入口，不计入已归档）</h4>
        <p v-for="rev in dashboard?.pending_revisions ?? []" :key="rev.revision_id" class="side-line">
          {{ rev.revision_id }} → {{ rev.window_id }} 更正值 {{ rev.value }}
          <button class="link" type="button" :data-testid="`accept-${rev.revision_id}`" @click="onAccept(rev.revision_id)">
            验收
          </button>
        </p>
        <p v-if="!dashboard?.pending_revisions.length" class="side-line muted">无待核修订</p>
      </aside>
    </div>

    <h3 class="section-title">范围查询与重算</h3>
    <div class="flow-panel">
      <div class="flow-actions">
        <button class="btn primary" data-testid="prepare-query" type="button" @click="onPrepare">准备查询入口</button>
        <span v-if="queryEntry" class="flow-state" data-testid="query-state">
          {{ queryEntry.query_id }}：{{ queryEntry.state }}（{{ queryEntry.delivered ? '已送达读者' : '尚未发给读者' }}）
        </span>
        <button class="btn" data-testid="read-query" type="button" :disabled="!queryEntry" @click="onRead">
          完成范围查询
        </button>
      </div>

      <table v-if="queryResult" class="data-table" data-testid="index-table">
        <thead>
          <tr><th>索引编号</th><th>窗口编号</th><th>点区间</th><th>存储位置</th><th>有效值</th><th>已归档</th><th>重算</th></tr>
        </thead>
        <tbody>
          <tr v-for="entry in queryResult.index" :key="entry.索引编号">
            <td>{{ entry.索引编号 }}</td>
            <td>{{ entry.窗口编号 }}</td>
            <td>{{ entry.点区间 }}</td>
            <td>{{ entry.存储位置 }}</td>
            <td>{{ entry.有效值 }}</td>
            <td>{{ entry.已归档 ? '已归档' : '未归档' }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`recompute-${entry.窗口编号}`" @click="onRecompute(entry.索引编号)">
                开始重算
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <div class="flow-actions">
        <button class="btn ghost" data-testid="interrupt" type="button" @click="onInterrupt">模拟归档中断</button>
        <button class="btn" data-testid="resume" type="button" @click="onResume">按上次已确认窗口续算</button>
      </div>

      <div v-if="recomputeResult" class="recompute-result" data-testid="recompute-result">
        <p>
          重算窗口 <strong>{{ recomputeResult.窗口编号 }}</strong>：实际采用值
          <strong data-testid="adopted-value">{{ recomputeResult.adopted_value }}</strong>，
          保存位置 <strong data-testid="adopted-location">{{ recomputeResult.location }}</strong>，
          {{ recomputeResult.archived ? '已归档' : '未归档（未迁冷，不得说成已归档）' }}
        </p>
        <p v-if="recomputeResult.resumed_from">
          归档曾中断，按上次已确认窗口 {{ recomputeResult.resumed_from }} 继续重算。
        </p>
        <p>等待逐项核验的窗口：{{ recomputeResult.pending_windows.join('、') || '无' }}</p>
      </div>
    </div>

    <h3 class="section-title">校验清单</h3>
    <div class="flow-panel">
      <div class="flow-actions">
        <button class="btn primary" data-testid="generate-checklist" type="button" @click="onGenerateChecklist">
          生成下一轮校验清单
        </button>
        <span v-if="checklist" class="flow-state">
          {{ checklist.checklist_id }}（第 {{ checklist.round }} 轮）；
          已通过校验不再排入：{{ checklist.excluded.map((e) => e.窗口编号).join('、') || '无' }}
        </span>
      </div>
      <table v-if="checklist" class="data-table" data-testid="checklist-table">
        <thead>
          <tr><th>窗口编号</th><th>副本</th><th>保存位置</th><th>副本值</th><th>逐项核验</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in checklist.items" :key="item.copy_id">
            <td>{{ item.窗口编号 }}</td>
            <td>{{ item.copy_id }}</td>
            <td>{{ item.location }}</td>
            <td>{{ item.value }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`verify-ok-${item.copy_id}`" @click="onVerify(item, true)">通过</button>
              <button class="link" type="button" :data-testid="`verify-fail-${item.copy_id}`" @click="onVerify(item, false)">失败</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="verifyRecords.length" class="verify-records">
        <p v-for="(record, idx) in verifyRecords" :key="idx" :class="record.ok ? 'side-line' : 'side-line error-text'">
          {{ record.窗口编号 }} / {{ record.copy_id }}：{{ record.ok ? '核验通过' : `核验失败（失败副本 ${record.失败副本}）${record.note ? '——' + record.note : ''}` }}
        </p>
      </div>
    </div>

    <footer class="page-foot">
      <span>归档回查视图与序列索引共享同一覆盖结果</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  acceptRevision,
  generateChecklist,
  interruptArchive,
  loadCoverage,
  loadDashboard,
  prepareQuery,
  readQuery,
  recomputeWindow,
  verifyCopy,
  type Checklist,
  type CoverageResult,
  type DashboardResult,
  type QueryEntry,
  type QueryReadResult,
  type RecomputeResult,
  type VerifyRecord,
} from '@/domain/archive_recall'

const partition = ref('2026-09-01')
const coverage = ref<CoverageResult | null>(null)
const dashboard = ref<DashboardResult | null>(null)
const queryEntry = ref<QueryEntry | null>(null)
const queryResult = ref<QueryReadResult | null>(null)
const recomputeResult = ref<RecomputeResult | null>(null)
const checklist = ref<Checklist | null>(null)
const errorMessage = ref('')

const hotZone = computed(() => dashboard.value?.热区水位 ?? coverage.value?.热区水位 ?? null)
const verifyRecords = computed<VerifyRecord[]>(() => dashboard.value?.verify_records ?? [])

async function reload() {
  errorMessage.value = ''
  try {
    const [cov, dash] = await Promise.all([loadCoverage(partition.value), loadDashboard()])
    coverage.value = cov
    dashboard.value = dash
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '看板读取失败'
  }
}

async function guard(action: () => Promise<void>) {
  errorMessage.value = ''
  try {
    await action()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function onPrepare() {
  await guard(async () => {
    queryEntry.value = await prepareQuery(partition.value)
    queryResult.value = null
    recomputeResult.value = null
  })
}

async function onRead() {
  if (!queryEntry.value) return
  await guard(async () => {
    queryResult.value = await readQuery(queryEntry.value!.query_id)
    queryEntry.value = { ...queryEntry.value!, state: queryResult.value.state, delivered: true }
    coverage.value = queryResult.value.coverage
    dashboard.value = await loadDashboard()
  })
}

async function onAccept(revisionId: string) {
  await guard(async () => {
    await acceptRevision(revisionId)
    await reload()
  })
}

async function onRecompute(indexHandle: string) {
  await guard(async () => {
    recomputeResult.value = await recomputeWindow({ partition: partition.value, indexHandle })
  })
}

async function onInterrupt() {
  await guard(async () => {
    await interruptArchive()
  })
}

async function onResume() {
  await guard(async () => {
    recomputeResult.value = await recomputeWindow({ partition: partition.value, resume: true })
  })
}

async function onGenerateChecklist() {
  await guard(async () => {
    checklist.value = await generateChecklist(partition.value)
  })
}

async function onVerify(item: Checklist['items'][number], ok: boolean) {
  await guard(async () => {
    await verifyCopy(item.窗口编号, item.copy_id, ok, ok ? '' : '校验值不匹配')
    checklist.value = await generateChecklist(partition.value)
    await reload()
  })
}

onMounted(reload)
</script>

<style scoped>
.section-title { margin: 18px 0 8px; font-size: 15px; }
.partition-picker span { display: block; font-size: 12px; color: var(--muted); }
.partition-picker input { border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; }
.warn-text { color: #b45309; font-size: 12px; }
.trace-chip {
  display: inline-block; margin: 2px 4px 2px 0; padding: 2px 6px;
  background: #eef2ff; border-radius: 4px; font-size: 12px;
}
.board-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; align-items: start; }
.side-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.side-panel h4 { margin: 8px 0 6px; font-size: 13px; }
.side-line { margin: 4px 0; font-size: 13px; }
.side-line.muted { color: var(--muted); }
.flow-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 8px; }
.flow-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.flow-state { font-size: 13px; color: var(--muted); }
.recompute-result { border-top: 1px solid var(--border); padding-top: 8px; font-size: 13px; }
.verify-records { border-top: 1px solid var(--border); padding-top: 8px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
