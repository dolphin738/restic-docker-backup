<script setup lang="ts">
/** 任务历史页（页面 7）：进行中视图（P1 既有，E13 显式 running/pending）+ 全量历史视图（P2 批次 A）
 *  + check 校验结果视图（批次 B：E22 = 错误列表 + summary 指标，V4 无进度集）。
 *
 * E13 完整版缺省语义=不过滤，故历史视图与「进行中」卡片视图分开：
 * 历史视图提供 type / status（含终态）/ task_id / 开始时间范围筛选 + 分页表格（50/页，上限 500）。
 * 日志抽屉（E27 尾部逐级放大 + skipped 明细）两视图共用；check 行进 E22 详情抽屉。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NDescriptions,
  NDescriptionsItem,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NCollapse,
  NCollapseItem,
  NLog,
  NPopconfirm,
  NSelect,
  NSpace,
  NTabPane,
  NTabs,
  NTag,
  NText,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { listBackupTasks } from '@/api/backupTasks'
import { cancelJob, createJob, getCheckDetail, getJobLog, listActiveJobs, listJobsHistory } from '@/api/jobs'
import type { CheckDetailResponse, JobOut, JobStatus, JobType } from '@/api/types'
import { useUiStore } from '@/stores/ui'
import { isAppError } from '@/api/http'
import ConfirmGate from '@/components/ConfirmGate.vue'
import JobProgress from '@/components/JobProgress.vue'
import { formatDateTime } from '@/utils/datetime'

const { t } = useI18n()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

/** 视图切换：active = 进行中卡片（P1 既有）；history = 全量筛选表格（P2 批次 A）。 */
const viewTab = ref<'active' | 'history'>('active')

// ================================================================ 进行中视图（P1 既有，零行为变更）

const jobsQuery = useQuery({
  queryKey: ['jobs', 'active'],
  queryFn: () => listActiveJobs(50),
  refetchInterval: 15_000,
})

const jobs = computed(() => jobsQuery.data.value?.items ?? [])

// ---------------------------------------------------------------- 取消
const cancellingId = ref('')

async function onCancel(jobId: string): Promise<void> {
  cancellingId.value = jobId
  try {
    await cancelJob(jobId)
    message.success(t('jobs.cancelSubmitted'))
    await jobsQuery.refetch()
  } finally {
    cancellingId.value = ''
  }
}

// ================================================================ 全量历史视图（E13 完整版）

const JOB_TYPES: JobType[] = ['backup', 'restore', 'forget', 'check', 'unlock', 'init', 'dump']
const JOB_STATUSES: JobStatus[] = ['running', 'pending', 'success', 'failed', 'interrupted', 'cancelled']

const HISTORY_PAGE_SIZE = 50

// 草稿态（编辑中，点「查询」生效）与应用态（实际请求参数）分离，避免每敲一步就打一次接口
const draftType = ref<JobType | null>(null)
const draftStatuses = ref<JobStatus[]>([])
const draftTaskId = ref<number | null>(null)
const draftRange = ref<[number, number] | null>(null)

const appliedType = ref<JobType | null>(null)
const appliedStatuses = ref<JobStatus[]>([])
const appliedTaskId = ref<number | null>(null)
const appliedSince = ref<string | undefined>(undefined)
const appliedUntil = ref<string | undefined>(undefined)
const historyPage = ref(1)

function applyHistoryFilters(): void {
  appliedType.value = draftType.value
  appliedStatuses.value = [...draftStatuses.value]
  appliedTaskId.value = draftTaskId.value
  appliedSince.value = draftRange.value ? new Date(draftRange.value[0]).toISOString() : undefined
  appliedUntil.value = draftRange.value ? new Date(draftRange.value[1]).toISOString() : undefined
  historyPage.value = 1
}

function resetHistoryFilters(): void {
  draftType.value = null
  draftStatuses.value = []
  draftTaskId.value = null
  draftRange.value = null
  applyHistoryFilters()
}

const typeOptions = computed(() => JOB_TYPES.map((value) => ({ label: t(`jobs.type.${value}`), value })))
const statusOptions = computed(() => JOB_STATUSES.map((value) => ({ label: t(`jobs.status.${value}`), value })))

/** task_id 下拉数据源：E34 任务列表（与快照页筛选同一来源与展示形态）。 */
const tasksQuery = useQuery({
  queryKey: ['backup-tasks', 'history-filter'],
  queryFn: () => listBackupTasks(50),
})
const taskOptions = computed(() =>
  (tasksQuery.data.value?.items ?? []).map((task) => ({
    // 括号走 i18n 词条（en 界面不露全角标点）
    label: t('jobs.taskOptionLabel', { name: task.name, tag: task.tag }),
    value: task.id,
  })),
)

const historyQuery = useQuery({
  queryKey: [
    'jobs',
    'history',
    appliedType,
    appliedStatuses,
    appliedTaskId,
    appliedSince,
    appliedUntil,
    historyPage,
  ],
  queryFn: () =>
    listJobsHistory({
      type: appliedType.value ?? undefined,
      statuses: appliedStatuses.value,
      taskId: appliedTaskId.value ?? undefined,
      startedFrom: appliedSince.value,
      startedTo: appliedUntil.value,
      limit: HISTORY_PAGE_SIZE,
      offset: (historyPage.value - 1) * HISTORY_PAGE_SIZE,
    }),
  placeholderData: (previous) => previous,
  enabled: computed(() => viewTab.value === 'history'),
})

const historyRows = computed(() => historyQuery.data.value?.items ?? [])
const historyTotal = computed(() => historyQuery.data.value?.total ?? 0)

const historyPagination = computed(() => ({
  page: historyPage.value,
  pageSize: HISTORY_PAGE_SIZE,
  itemCount: historyTotal.value,
  onChange: (value: number) => {
    historyPage.value = value
  },
  prefix: () => t('jobs.historyTotal', { n: historyTotal.value }),
}))

function jobStatusTagType(status: JobStatus): 'default' | 'info' | 'success' | 'warning' | 'error' {
  switch (status) {
    case 'running':
    case 'pending':
      return 'info'
    case 'success':
      return 'success'
    case 'failed':
      return 'error'
    case 'interrupted':
    case 'cancelled':
      return 'warning'
    default:
      return 'default'
  }
}

const historyColumns = computed<DataTableColumns<JobOut>>(() => [
  {
    title: t('jobs.colType'),
    key: 'type',
    width: 110,
    render: (row) => h(NTag, { size: 'small', round: true }, () => t(`jobs.type.${row.type}`)),
  },
  {
    title: t('jobs.colStatus'),
    key: 'status',
    width: 100,
    render: (row) =>
      h(
        NTag,
        { size: 'small', round: true, type: jobStatusTagType(row.status) },
        () => t(`jobs.status.${row.status}`),
      ),
  },
  { title: t('jobs.colTask'), key: 'task_id', width: 80, render: (row) => row.task_id ?? '—' },
  {
    title: t('jobs.colStarted'),
    key: 'started_at',
    width: 170,
    render: (row) => formatDateTime(row.started_at),
  },
  {
    title: t('jobs.colFinished'),
    key: 'finished_at',
    width: 170,
    render: (row) => formatDateTime(row.finished_at),
  },
  {
    title: t('jobs.colSummary'),
    key: 'summary',
    ellipsis: { tooltip: true },
    render: (row) => row.summary ?? '—',
  },
  {
    title: t('jobs.colActions'),
    key: 'actions',
    width: 150,
    render: (row) =>
      h(NSpace, { size: 4, wrap: false }, () => {
        const buttons = [
          h(
            NButton,
            { size: 'tiny', secondary: true, onClick: () => openLog(row) },
            () => t('job.viewLog'),
          ),
        ]
        // check 任务行进 E22 详情视图（错误列表 + summary 指标，V4 无进度集）
        if (row.type === 'check') {
          buttons.unshift(
            h(
              NButton,
              { size: 'tiny', secondary: true, type: 'primary', onClick: () => openCheckDetail(row) },
              () => t('jobs.check.resultButton'),
            ),
          )
        }
        return buttons
      }),
  },
])

// ================================================================ check 校验结果视图（E22，批次 B）

const checkShow = ref(false)
const checkLoading = ref(false)
const checkError = ref('')
const checkDetail = ref<CheckDetailResponse | null>(null)

/** E22 silent 请求：错误内联展示在抽屉中（避免全局 toast 与内联双重提示）。 */
function openCheckDetail(job: JobOut): void {
  checkShow.value = true
  checkLoading.value = true
  checkError.value = ''
  checkDetail.value = null
  getCheckDetail(job.id)
    .then((data) => {
      checkDetail.value = data
    })
    .catch((error: unknown) => {
      checkError.value = error instanceof Error ? error.message : String(error)
    })
    .finally(() => {
      checkLoading.value = false
    })
}

const checkView = computed(() => checkDetail.value?.check_view ?? null)
/** 告警口径用 error_count（错误行数）：截断场景可能少于 summary 的 num_errors（后端契约注）。 */
const checkHasErrors = computed(() => (checkView.value?.error_count ?? 0) > 0)
/** broken_packs 为保序 pack id 列表（健康为空列表，后端已归一 null）。 */
const brokenPacks = computed(() => checkView.value?.broken_packs ?? [])
const brokenPacksCount = computed(() => brokenPacks.value.length)
/** 仅当损坏 pack 存在且无其他动作建议时，追加「检查仓库完整性」提示（契约对齐口径）。 */
const brokenPacksOnlyHint = computed(
  () => brokenPacksCount.value > 0 && !checkView.value?.suggest_repair_index && !checkView.value?.suggest_prune,
)

function checkBoolText(value: boolean): string {
  return value ? t('jobs.check.yes') : t('jobs.check.no')
}

// B2 自愈建议链：check 报告 structural 损坏（suggest_repair_index）→ 一键 repair 入口。
// 复用 ConfirmGate（关键词 REPAIR，不可逆提示）；仓库维度修复任务统一在「仓库配置」执行，
// 此处只负责把「建议」变成「可点击动作」，避免用户在两个页面间反复对照。
const repairShow = ref(false)
const repairing = ref(false)
const repairJobId = ref('')

async function onRepair(): Promise<void> {
  repairing.value = true
  try {
    const created = await createJob('repair', { params: { repair_target: 'index' } })
    repairJobId.value = created.id
    message.success(t('jobs.check.repairSubmitted'))
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    repairing.value = false
  }
}

// ================================================================ 日志抽屉（E27，两视图共用）

interface LogState {
  jobId: string
  content: string
  /** 实际已拉取字节数（响应 tail_bytes）；undefined 表示还没拉过（用服务端默认 64 KiB）。 */
  tailBytes: number | null
  truncated: boolean
  loading: boolean
  loadingMore: boolean
  /** 该 job 的 skipped 计数（E14 口径），用于 skipped 明细折叠区标题与开关。 */
  skippedCount: number
}

const DEFAULT_TAIL = 65_536
const MAX_TAIL = 1_048_576

const logShow = ref(false)
const log = ref<LogState>({
  jobId: '',
  content: '',
  tailBytes: null,
  truncated: false,
  loading: false,
  loadingMore: false,
  skippedCount: 0,
})

function nextTailSize(current: number | null): number {
  if (current === null) {
    return DEFAULT_TAIL * 4 // 64 KiB → 256 KiB
  }
  return Math.min(current * 2, MAX_TAIL)
}

function openLog(job: JobOut): void {
  logShow.value = true
  log.value = {
    jobId: job.id,
    content: '',
    tailBytes: null,
    truncated: false,
    loading: true,
    loadingMore: false,
    skippedCount: job.skipped_count,
  }
  void getJobLog(job.id)
    .then((data) => {
      log.value = {
        jobId: job.id,
        content: data.content,
        tailBytes: data.tail_bytes,
        truncated: data.truncated,
        loading: false,
        loadingMore: false,
        skippedCount: job.skipped_count,
      }
    })
    .catch(() => {
      log.value.loading = false
    })
}

/** 逐级放大尾部窗口（64 KiB → 256 KiB → … → 1 MiB 上限）。 */
async function loadMoreLog(): Promise<void> {
  if (log.value.loadingMore || !log.value.truncated) {
    return
  }
  const target = nextTailSize(log.value.tailBytes)
  if (log.value.tailBytes !== null && log.value.tailBytes >= MAX_TAIL) {
    return
  }
  log.value.loadingMore = true
  try {
    const data = await getJobLog(log.value.jobId, target)
    log.value.content = data.content
    log.value.tailBytes = data.tail_bytes
    log.value.truncated = data.truncated
  } finally {
    log.value.loadingMore = false
  }
}

const canLoadMore = computed(
  () => log.value.truncated && (log.value.tailBytes === null || log.value.tailBytes < MAX_TAIL),
)

// ---------------------------------------------------------------- skipped 明细（数量取 E14，明细从 E27 文本挑）
interface SkippedEntry {
  item: string
  error: string
}

const skippedEntries = computed<SkippedEntry[]>(() => {
  const entries: SkippedEntry[] = []
  for (const line of log.value.content.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('{')) {
      continue
    }
    try {
      const payload = JSON.parse(trimmed) as Record<string, unknown>
      if (payload['message_type'] !== 'error' || payload['during'] !== 'archival') {
        continue
      }
      const item = typeof payload['item'] === 'string' ? payload['item'] : trimmed
      entries.push({
        item,
        error: typeof payload['error'] === 'string' ? payload['error'] : '',
      })
    } catch {
      // 非完整 JSON 行（半行截断）跳过
    }
  }
  return entries
})
</script>

<template>
  <n-space vertical :size="16">
    <n-tabs v-model:value="viewTab" type="line" size="small" animated>
      <!-- ================= 进行中（P1 既有卡片视图，零行为变更） ================= -->
      <n-tab-pane name="active" :tab="t('jobs.tabActive')">
        <n-alert v-if="jobsQuery.error.value" type="error" :show-icon="false">
          {{ String(jobsQuery.error.value) }}
        </n-alert>

        <template v-if="jobs.length">
          <n-card v-for="job in jobs" :key="job.id" size="small">
            <n-space vertical :size="10">
              <n-space align="center" justify="space-between">
                <n-space align="center" :size="8">
                  <n-tag size="small" round>{{ t(`jobs.type.${job.type}`) }}</n-tag>
                  <n-tag size="small" round :type="jobStatusTagType(job.status)">
                    {{ t(`jobs.status.${job.status}`) }}
                  </n-tag>
                  <n-text depth="3">{{ formatDateTime(job.created_at) }}</n-text>
                </n-space>
                <n-space :size="6">
                  <n-button size="tiny" secondary @click="openLog(job)">{{ t('job.viewLog') }}</n-button>
                  <n-popconfirm @positive-click="onCancel(job.id)">
                    <template #trigger>
                      <n-button size="tiny" type="warning" :loading="cancellingId === job.id">
                        {{ t('jobs.cancel') }}
                      </n-button>
                    </template>
                    {{ t('jobs.cancelConfirm') }}
                  </n-popconfirm>
                </n-space>
              </n-space>
              <!-- V4：check --json 无进度事件，check 任务不建进度条，仅运行状态 + 日志 -->
              <JobProgress :job-id="job.id" :title="`${job.type} · ${job.status}`" :hide-progress-bar="job.type === 'check'" />
              <!-- 备份类任务：首次最慢属预期，小字提示不抢焦点 -->
              <n-text v-if="job.type === 'backup'" depth="3">{{ t('jobs.firstBackupSlowNote') }}</n-text>
            </n-space>
          </n-card>
        </template>
        <n-card v-else size="small">
          <n-empty :description="t('dashboard.jobsEmpty')" />
        </n-card>
      </n-tab-pane>

      <!-- ================= 全量历史（E13 完整版：缺省不过滤，条件显式传入） ================= -->
      <n-tab-pane name="history" :tab="t('jobs.tabHistory')">
        <n-space vertical :size="10">
          <n-text depth="3">{{ t('jobs.historyLimitNote', { n: HISTORY_PAGE_SIZE }) }}</n-text>

          <!-- 筛选区：type / status（含终态）/ task_id / 开始时间范围 -->
          <n-card size="small">
            <n-space align="center" :size="10" class="jobs__filters">
              <n-select
                v-model:value="draftType"
                :options="typeOptions"
                clearable
                :placeholder="t('jobs.allTypes')"
                size="small"
                class="jobs__filter-select"
              />
              <n-select
                v-model:value="draftStatuses"
                :options="statusOptions"
                multiple
                clearable
                :placeholder="t('jobs.allStatuses')"
                size="small"
                class="jobs__filter-select jobs__filter-select--wide"
              />
              <n-select
                v-model:value="draftTaskId"
                :options="taskOptions"
                clearable
                filterable
                :placeholder="t('jobs.allTasks')"
                size="small"
                class="jobs__filter-select jobs__filter-select--wide"
              />
              <n-date-picker
                v-model:value="draftRange"
                type="datetimerange"
                clearable
                size="small"
                class="jobs__filter-range"
              />
              <n-button size="small" type="primary" :loading="historyQuery.isFetching.value" @click="applyHistoryFilters">
                {{ t('jobs.apply') }}
              </n-button>
              <n-button size="small" quaternary @click="resetHistoryFilters">{{ t('jobs.reset') }}</n-button>
            </n-space>
          </n-card>

          <n-alert v-if="historyQuery.error.value" type="error" :show-icon="false">
            {{ String(historyQuery.error.value) }}
          </n-alert>

          <n-data-table
            size="small"
            :columns="historyColumns"
            :data="historyRows"
            :loading="historyQuery.isFetching.value"
            :pagination="historyPagination"
            :row-key="(row: JobOut) => row.id"
          />
          <n-text v-if="!historyQuery.isFetching.value && historyRows.length === 0" depth="3">
            {{ t('jobs.historyEmpty') }}
          </n-text>
        </n-space>
      </n-tab-pane>
    </n-tabs>

    <!-- check 校验结果抽屉（E22）：错误列表 + summary 指标；不做进度条（V4 定稿） -->
    <n-drawer v-model:show="checkShow" :width="680" placement="right">
      <n-drawer-content :title="t('jobs.check.detailTitle')" closable>
        <n-space vertical :size="12">
          <n-alert v-if="checkError" type="error" :show-icon="false">{{ checkError }}</n-alert>
          <n-alert v-else-if="checkHasErrors" type="error" :show-icon="false">
            {{ t('jobs.check.failed', { n: checkView?.error_count ?? 0 }) }}
          </n-alert>
          <n-alert v-else-if="checkView && !checkLoading && checkDetail?.status === 'success'" type="success" :show-icon="false">
            {{ t('jobs.check.healthy') }}
          </n-alert>
          <!-- 任务取消/失败但无错误行：不得显示绿色「校验通过」（QA 复验 P2），降级为中性提示 -->
          <n-alert v-else-if="checkView && !checkLoading" type="warning" :show-icon="false">
            {{ t('jobs.check.notSuccess', { status: checkDetail?.status ?? '' }) }}
          </n-alert>

          <!-- 下一步动作建议（验收要点：check 失败醒目提示 + 下一步动作）；损坏 pack 独存时补完整性提示 -->
          <n-space v-if="checkView && (checkHasErrors || brokenPacksCount > 0)" vertical :size="4">
            <n-text strong>{{ t('jobs.check.nextSteps') }}</n-text>
            <n-text v-if="checkView.suggest_repair_index" depth="2">
              · {{ t('jobs.check.suggestRepairIndex') }}
            </n-text>
            <n-text v-if="checkView.suggest_prune" depth="2">
              · {{ t('jobs.check.suggestPrune') }}
            </n-text>
            <n-text v-if="brokenPacksOnlyHint" depth="2">
              · {{ t('jobs.check.brokenPacksHint') }}
            </n-text>
            <!-- B2 一键 repair 建议链：仅当 check 明确建议重建索引时给出可点击动作 -->
            <n-button
              v-if="checkView.suggest_repair_index"
              size="small"
              type="error"
              :loading="repairing"
              @click="repairShow = true"
            >
              {{ t('jobs.check.repairBtn') }}
            </n-button>
          </n-space>

          <n-descriptions v-if="checkView" :column="2" size="small" bordered>
            <n-descriptions-item :label="t('jobs.check.errorCount')">
              <n-tag size="small" round :type="checkHasErrors ? 'error' : 'success'">
                {{ checkView.error_count }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item :label="t('jobs.check.summaryNumErrors')">
              {{ checkView.num_errors }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('jobs.check.readData')">
              {{ checkBoolText(checkView.read_data) }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('jobs.check.suggestRepairIndexShort')">
              {{ checkBoolText(checkView.suggest_repair_index) }}
            </n-descriptions-item>
          </n-descriptions>

          <!-- broken_packs：保序 pack id 列表（64hex）；计数进 summary 口径，id 清单等宽限高展开 -->
          <template v-if="brokenPacksCount > 0">
            <n-text strong>{{ t('jobs.check.brokenPacksCount', { n: brokenPacksCount }) }}</n-text>
            <n-text depth="3">{{ t('jobs.check.brokenPacksList', { n: brokenPacksCount }) }}</n-text>
            <div class="jobs__check-packs">
              <div v-for="packId in brokenPacks" :key="packId" class="jobs__check-pack">{{ packId }}</div>
            </div>
          </template>

          <template v-if="checkView">
            <n-text strong>{{ t('jobs.check.errorList', { n: checkView.errors.length }) }}</n-text>
            <n-space v-if="checkView.errors.length > 0" vertical :size="4" class="jobs__check-errors">
              <div v-for="(item, index) in checkView.errors" :key="`${item.message}-${index}`" class="jobs__check-error">
                <n-text depth="2">{{ item.message }}</n-text>
              </div>
            </n-space>
          </template>

          <n-space v-if="checkDetail">
            <n-button size="small" secondary @click="openLog(checkDetail)">{{ t('job.viewLog') }}</n-button>
          </n-space>

          <!-- B2 自愈建议链：一键修复确认门（关键词 REPAIR，重建索引不可并发读写） -->
          <ConfirmGate
            v-model:show="repairShow"
            :title="t('jobs.check.repairBtn')"
            :keyword="'REPAIR'"
            :description="t('jobs.check.repairDesc')"
            :loading="repairing"
            @confirm="onRepair"
          >
            <template #preview>
              <n-text depth="3">{{ t('jobs.check.repairPreview') }}</n-text>
            </template>
          </ConfirmGate>
        </n-space>
      </n-drawer-content>
    </n-drawer>

    <!-- 日志抽屉（两视图共用）：首次默认 64 KiB，按需逐级放大到 1 MiB -->
    <n-drawer v-model:show="logShow" :width="760" placement="right">
      <n-drawer-content :title="t('jobs.logTitle')" closable>
        <n-space vertical :size="10">
          <template v-if="log.skippedCount > 0">
            <n-collapse size="small">
              <n-collapse-item :title="t('job.skippedTitle', { n: log.skippedCount })" name="skipped">
                <n-space vertical :size="6">
                  <n-text depth="3">{{ t('jobs.skippedTailNote') }}</n-text>
                  <template v-if="skippedEntries.length">
                    <div v-for="(entry, index) in skippedEntries" :key="`${entry.item}-${index}`" class="jobs__skipped">
                      <n-text depth="2">{{ entry.item }}</n-text>
                      <n-text v-if="entry.error" depth="3">{{ entry.error }}</n-text>
                    </div>
                  </template>
                  <n-text v-else depth="3">{{ t('jobs.skippedNotInTail') }}</n-text>
                </n-space>
              </n-collapse-item>
            </n-collapse>
          </template>

          <n-log :log="log.content || t('job.logEmpty')" :rows="26" trim />
          <n-text depth="3">
            {{ t('jobs.logTailNote', { n: log.tailBytes ?? 0 }) }}
          </n-text>
          <n-space>
            <n-button
              v-if="canLoadMore"
              size="small"
              secondary
              :loading="log.loadingMore"
              @click="loadMoreLog"
            >
              {{ t('jobs.logLoadMore') }}
            </n-button>
            <n-text v-else-if="log.truncated" depth="3">{{ t('jobs.logMaxTail') }}</n-text>
          </n-space>
        </n-space>
      </n-drawer-content>
    </n-drawer>
  </n-space>
</template>

<style scoped>
.jobs__skipped {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 8px;
  border-left: 2px solid rgba(250, 208, 44, 0.6);
  font-size: 12px;
}

.jobs__check-errors {
  max-height: 320px;
  overflow: auto;
}

.jobs__check-error {
  padding: 4px 8px;
  border-left: 2px solid rgba(192, 54, 54, 0.6);
  font-size: 12px;
}

.jobs__check-packs {
  max-height: 200px;
  overflow: auto;
  padding: 8px 12px;
  font-family: 'Cascadia Mono', 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}

.jobs__check-pack {
  white-space: pre;
}

.jobs__filters {
  flex-wrap: wrap;
}

.jobs__filter-select {
  width: 180px;
}

.jobs__filter-select--wide {
  width: 240px;
}

.jobs__filter-range {
  width: 360px;
}
</style>
