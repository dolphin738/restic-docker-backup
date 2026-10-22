<script setup lang="ts">
/** 仪表盘（增量设计 §5.1 + 方案 §4.5 / §4.5.6 P7）：仓库状态卡 + 运行模式卡保留；
 * 层级按 §4.5.6 对齐：KPI 带 → 行动卡行（D1）→ 全局状态条（D2）→ 工具栏（D3/D4/D5）
 * → list-details 双视图 → 空态。
 *
 * **list 视图用 `TaskRowCard` 行卡**（§4.5.6:292 组件规划原文；D8 里「list 视图经 TaskCard」
 * 的表述已被 owner 2026-10-23 判定为错，旧 `TaskCard` 随之退役删除）。
 */

import { useVirtualList } from '@vueuse/core'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NGi,
  NGrid,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, h, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { deleteBackupTask, exportBackupTask, listBackupTasks } from '@/api/backupTasks'
import { hasCode, isAppError } from '@/api/http'
import { cancelJob, createJob, listJobsHistory } from '@/api/jobs'
import { getRepoStatus } from '@/api/repo'
import type { TaskCard as TaskCardModel } from '@/api/types'
import ActionCard from '@/components/dashboard/ActionCard.vue'
import GlobalStatusBar from '@/components/dashboard/GlobalStatusBar.vue'
import SegmentedTabs from '@/components/dashboard/SegmentedTabs.vue'
import TaskActionsMenu from '@/components/dashboard/TaskActionsMenu.vue'
import TaskRowCard from '@/components/dashboard/TaskRowCard.vue'
import ConfirmGate from '@/components/ConfirmGate.vue'
import JobLogDrawer from '@/components/JobLogDrawer.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useCountUp } from '@/composables/useCountUp'
import { useUiStore } from '@/stores/ui'
import { formatDateTimeOr } from '@/utils/datetime'
import { formatBytes } from '@/utils/format'
import { formatDurationMs, statusKind, timeText } from '@/utils/taskFormat'
import { IconFolderOpen, IconPlusCircle } from '@/components/icons/HomeIcons'

const { t } = useI18n()
const router = useRouter()
const ui = useUiStore()
const queryClient = useQueryClient()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

// E30 服务端 30s 缓存：这里按 60s 轮询 + 手动刷新，禁止秒级轮询
const repoQuery = useQuery({
  queryKey: ['repo', 'status', ui.currentRepoId],
  queryFn: () => getRepoStatus(ui.currentRepoId),
  refetchInterval: 60_000,
})

// E34 任务卡片列表：15s 轮询与旧「进行中任务卡」同节奏；运行中进度由卡片内 SSE 实时推进
const tasksQuery = useQuery({
  queryKey: ['backup-tasks'],
  queryFn: () => listBackupTasks(50),
  refetchInterval: 15_000,
})

const repoStatus = computed(() => repoQuery.data.value ?? null)
const repoLoading = computed(() => repoQuery.isFetching.value)
const repoError = computed(() => toText(repoQuery.error.value))
const tasks = computed<TaskCardModel[]>(() => tasksQuery.data.value?.items ?? [])
const tasksError = computed(() => toText(tasksQuery.error.value))

// ---------------------------------------------------------------- KPI 状态带（P4：三卡合一）
/** 运行用户档位（A0 后只按运行用户分档；与 TierCard 同口径，仪表盘以 KPI 单元格呈现）。 */
const userKey = computed<'root' | 'nonRoot'>(() => (ui.me?.user === 'root' ? 'root' : 'nonRoot'))

/** 最近一次成功备份：任务列表派生（无专用端点；§13.1 零后端改动）。 */
const lastBackupText = computed(() => {
  const successes = tasks.value.filter((task) => task.last_result === 'success' && task.last_run_at)
  if (successes.length === 0) {
    return t('common.none')
  }
  const latest = successes.reduce((a, b) => (Date.parse(b.last_run_at!) > Date.parse(a.last_run_at!) ? b : a))
  return formatDateTimeOr(latest.last_run_at, t('common.none'))
})

/** count-up（§11 AB-2）：一次性；空值不动画，回落占位文案。 */
const capacityBytes = computed(() => repoStatus.value?.capacity.size ?? null)
const snapsCount = computed(() => repoStatus.value?.capacity.snaps ?? null)
const capacityCount = useCountUp(computed(() => capacityBytes.value ?? 0))
const snapsDisplay = useCountUp(computed(() => snapsCount.value ?? 0))
const capacityText = computed(() =>
  capacityBytes.value === null ? t('common.none') : formatBytes(capacityCount.value),
)
const snapsText = computed(() => (snapsCount.value === null ? t('common.none') : String(snapsDisplay.value)))

// ---------------------------------------------------------------- 全局状态条（D2 / P7 ②裁决）
/** 当前进行中作业：取列表里首个带 running_job_id 的任务（JOB_CONFLICT 守卫保证同时至多一个）。 */
const runningTask = computed(() => tasks.value.find((task) => task.running_job_id) ?? null)
const globalJobId = computed(() => runningTask.value?.running_job_id ?? null)
const globalTitle = computed(() => runningTask.value?.name ?? '')
/** 任务级限速只读文案（owner 2026-10-22 任务级裁决：运行中不可改，仅展示）。 */
const globalLimitText = computed(() => {
  const task = runningTask.value
  if (!task) {
    return ''
  }
  const up = task.limit_up ?? 0
  const down = task.limit_down ?? 0
  return up === 0 && down === 0
    ? t('dashboard.statusBar.unlimited')
    : t('dashboard.statusBar.limit', { up, down })
})

function onStopGlobal(): void {
  if (globalJobId.value) {
    void onCancel(globalJobId.value)
  }
}

/** Abort 二段（P7 D2 / §4.5.4）：跳过 SIGTERM 宽限直接 SIGKILL，服务端审计 job.abort。 */
function onAbortGlobal(): void {
  if (globalJobId.value) {
    void onCancel(globalJobId.value, true)
  }
}

// ---------------------------------------------------------------- 工具栏（D3/D4/D5）
/** 视图：list=卡片 / details=表格；localStorage 持久化（owner 裁决：视图切换持久化）。 */
const VIEW_KEY = 'dashboard:viewMode'
const TIME_KEY = 'dashboard:timeType'
const viewMode = ref<'list' | 'details'>(
  typeof localStorage !== 'undefined' && localStorage.getItem(VIEW_KEY) === 'details' ? 'details' : 'list',
)
const timeType = ref<'relative' | 'actual'>(
  typeof localStorage !== 'undefined' && localStorage.getItem(TIME_KEY) === 'actual' ? 'actual' : 'relative',
)
const sortKey = ref<'name' | 'last_run_at' | 'next_run_at' | 'status'>('name')

watch(viewMode, (v) => localStorage.setItem(VIEW_KEY, v))
watch(timeType, (v) => localStorage.setItem(TIME_KEY, v))

const viewItems = computed(() => [
  { id: 'list', label: t('dashboard.view.list') },
  { id: 'details', label: t('dashboard.view.details') },
])
const timeItems = computed(() => [
  { id: 'relative', label: t('dashboard.time.relative') },
  { id: 'actual', label: t('dashboard.time.actual') },
])
const sortOptions = computed(() => [
  { label: t('dashboard.sort.name'), value: 'name' },
  { label: t('dashboard.sort.lastRun'), value: 'last_run_at' },
  { label: t('dashboard.sort.nextRun'), value: 'next_run_at' },
  { label: t('dashboard.sort.status'), value: 'status' },
])

/** D5 纯前端排序（后端 listBackupTasks 无 order 参数）：空时间恒排末尾。 */
const sortedTasks = computed<TaskCardModel[]>(() => {
  const arr = [...tasks.value]
  const k = sortKey.value
  arr.sort((a, b) => {
    if (k === 'name') {
      return a.name.localeCompare(b.name)
    }
    if (k === 'status') {
      return a.status.localeCompare(b.status)
    }
    const av = a[k] ? Date.parse(a[k] as string) : null
    const bv = b[k] ? Date.parse(b[k] as string) : null
    if (av === null && bv === null) {
      return 0
    }
    if (av === null) {
      return 1
    }
    if (bv === null) {
      return -1
    }
    return av - bv
  })
  return arr
})

/** 表格时间列收口（D4 口径）：与行卡同一实现（utils/taskFormat 单一实现点）。 */
function tableTime(value: string | null, fallback: string): string {
  const mode = timeType.value === 'actual' ? 'actual' : 'relative'
  return timeText(value, mode, t, fallback)
}

/** 耗时列：null → 长破折号（表格需要占位，行卡则直接不渲染）。 */
function tableDuration(ms: number | null): string {
  if (ms === null || ms < 0) {
    return '—'
  }
  return formatDurationMs(ms) || '—'
}

const detailRowKey = (row: TaskCardModel): number => row.id

const detailColumns = computed(() => [
  { title: t('task.name'), key: 'name', minWidth: 160 },
  {
    title: t('task.status.label'),
    key: 'status',
    width: 120,
    render: (row: TaskCardModel) =>
      h(StatusTag, { kind: statusKind(row.status), label: t(`task.status.${row.status}`) }),
  },
  {
    title: t('task.last_run_label'),
    key: 'last_run_at',
    minWidth: 160,
    render: (row: TaskCardModel) => tableTime(row.last_run_at, t('common.none')),
  },
  {
    title: t('task.next_run'),
    key: 'next_run_at',
    minWidth: 160,
    render: (row: TaskCardModel) =>
      tableTime(row.next_run_at, row.enabled ? t('task.manual_only') : t('task.paused')),
  },
  {
    title: t('task.duration'),
    key: 'last_duration_ms',
    width: 110,
    render: (row: TaskCardModel) => tableDuration(row.last_duration_ms),
  },
  {
    title: t('dashboard.repoSnapshots'),
    key: 'snapshot_count',
    width: 110,
    render: (row: TaskCardModel) => (row.snapshot_count === null ? '—' : String(row.snapshot_count)),
  },
  {
    title: t('task.actions'),
    key: 'actions',
    width: 100,
    render: (row: TaskCardModel) => renderRowActions(row),
  },
])

/** details 行菜单分组：与 list 视图行卡同一口径（§4.5.6 三组 + Danger），两端零漂移。 */
function rowGroups(row: TaskCardModel) {
  const isActive = row.status === 'running' || row.status === 'pending'
  return [
    {
      actions: [
        { key: 'edit', label: t('task.edit') },
        { key: 'restore', label: t('task.restore') },
      ],
    },
    {
      label: t('task.groupAdvanced'),
      actions: [
        { key: 'verify', label: t('task.verify') },
        { key: 'export', label: t('task.export') },
      ],
    },
    {
      label: t('task.groupReporting'),
      actions: [{ key: 'log', label: t('task.logs') }],
    },
    {
      label: t('delete.title'),
      actions: [{ key: 'delete', label: t('task.delete'), disabled: isActive }],
    },
  ]
}

function onRowMenuSelect(key: string, row: TaskCardModel): void {
  if (key === 'edit') {
    onEdit(row.id)
  } else if (key === 'delete') {
    askDelete(row.id)
  } else if (key === 'restore') {
    goRestore()
  } else if (key === 'verify') {
    void onVerify(row.id)
  } else if (key === 'export') {
    askExport(row.id)
  } else if (key === 'log') {
    openTaskLog(row.id)
  }
}

/** details 表格操作区（D8：接入 TaskActionsMenu，与 list 视图 TaskCard 同三组菜单）。 */
function renderRowActions(row: TaskCardModel) {
  return h(TaskActionsMenu, {
    groups: rowGroups(row),
    ariaLabel: t('task.menu'),
    onSelect: (key: string) => onRowMenuSelect(key, row),
  })
}

// ---------------------------------------------------------------- 虚拟滚动（固定行高骨架，仅 list 视图）
/** 行卡固定骨架（§4.5.6 行卡：名称行 + 指标区，两行文字）：行高 84 + 间距 8。
 *  改动须同步 `.tasks__row` 的 CSS 高度，否则 useVirtualList 会错位。 */
const ROW_HEIGHT = 84
const ROW_GAP = 8
const ITEM_HEIGHT = ROW_HEIGHT + ROW_GAP
const MAX_VISIBLE = 6

const { containerProps, wrapperProps, list: virtualItems } = useVirtualList(sortedTasks, {
  itemHeight: ITEM_HEIGHT,
})

/** 列表较短时不出现内层滚动条：容器高度随条数伸缩（最多 6 行）。 */
const viewportHeight = computed(() => Math.max(ITEM_HEIGHT, Math.min(sortedTasks.value.length, MAX_VISIBLE) * ITEM_HEIGHT))

function toText(error: unknown): string {
  if (!error) {
    return ''
  }
  return isAppError(error) ? error.message : t('errors.UNKNOWN')
}

// ---------------------------------------------------------------- 任务操作
async function invalidateTasks(): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ['backup-tasks'] })
}

function goNewTask(): void {
  void router.push({ name: 'task-new' })
}

function goRestore(): void {
  void router.push({ name: 'restore' })
}

function onEdit(taskId: number): void {
  void router.push({ name: 'task-edit', params: { id: taskId } })
}

/** 立即运行：409 JOB_CONFLICT 的 data 带 running_task_name（US-026-04 AC2）。 */
async function onStart(taskId: number): Promise<void> {
  try {
    await createJob('backup', { taskId })
    message.success(t('task.startSubmitted'))
    await invalidateTasks()
  } catch (error) {
    if (hasCode(error, 'JOB_CONFLICT')) {
      const raw = isAppError(error) ? error.data?.['running_task_name'] : null
      const name = typeof raw === 'string' && raw ? raw : t('common.none')
      message.error(t('task.conflict', { name }))
      return
    }
    // C9（owner 2026-10-23 裁决）：不再 rethrow —— 调用点多为 `void onXxx()`，
    // 抛出会变成 unhandled rejection；统一落成用户可见的错误提示。
    message.error(toText(error))
  }
}

async function onCancel(jobId: string, force = false): Promise<void> {
  try {
    await cancelJob(jobId, force)
    message.success(force ? t('jobs.abortSubmitted') : t('jobs.cancelSubmitted'))
    await invalidateTasks()
  } catch (error) {
    // C9（owner 2026-10-23 裁决）：不再 rethrow —— 调用点多为 `void onXxx()`，
    // 抛出会变成 unhandled rejection；统一落成用户可见的错误提示。
    message.error(toText(error))
  }
}

/** 校验备份（P7 菜单 Verify）：`createJob('check')` → **跳 E22 校验结果视图**
 *  （方案 §4.5.4：「只缺菜单入口 + 结果跳转」；owner 2026-10-23 裁决 A6 认可 jobs 页
 *  `?job=<id>` 自动打开 check 详情抽屉为落点）。
 *  409 JOB_CONFLICT 与 onStart 同口径取 `data.running_task_name`。 */
async function onVerify(taskId: number): Promise<void> {
  try {
    const job = await createJob('check', { taskId })
    message.success(t('task.verifySubmitted'))
    await invalidateTasks()
    void router.push({ name: 'jobs', query: { job: job.id } })
  } catch (error) {
    if (hasCode(error, 'JOB_CONFLICT')) {
      const raw = isAppError(error) ? error.data?.['running_task_name'] : null
      const name = typeof raw === 'string' && raw ? raw : t('common.none')
      message.error(t('task.conflict', { name }))
      return
    }
    // C9（owner 2026-10-23 裁决）：不再 rethrow —— 调用点多为 `void onXxx()`，
    // 抛出会变成 unhandled rejection；统一落成用户可见的错误提示。
    message.error(toText(error))
  }
}

// ---------------------------------------------------------------- 导出（P7 菜单 Export，§4.5.4 裁决④）
/** 导出口令弹窗状态。口令只用于本地加密打包（后端 `build_export_pack`，≥10 位），
 *  **不落库、不进 URL**；成功后立刻清空，避免留在内存/剪贴板旁。 */
const exportTarget = ref<TaskCardModel | null>(null)
const exportShow = ref(false)
const exportPassword = ref('')
const exporting = ref(false)

function askExport(taskId: number): void {
  const target = tasks.value.find((task) => task.id === taskId) ?? null
  if (!target) {
    return
  }
  exportTarget.value = target
  exportPassword.value = ''
  exportShow.value = true
}

function closeExport(): void {
  exportShow.value = false
  exportPassword.value = ''
  exportTarget.value = null
}

/** 口令下限与后端 `ConfigExportRequest`（min_length=10）保持一致，前端先拦一道避免 422。 */
const exportDisabled = computed(() => exporting.value || exportPassword.value.length < 10)

async function onExportConfirmed(): Promise<void> {
  const target = exportTarget.value
  if (!target || exportDisabled.value) {
    return
  }
  exporting.value = true
  try {
    const pack = await exportBackupTask(target.id, exportPassword.value)
    const blob = new Blob([JSON.stringify(pack, null, 1)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `restic-web-task-${target.id}-${String(pack.created_at).slice(0, 10)}.json`
    anchor.click()
    URL.revokeObjectURL(url)
    message.success(t('task.exportDone'))
    closeExport()
  } catch (error) {
    // C9：不 rethrow（调用点多在 `void` 上下文，抛出即未处理 rejection）
    message.error(toText(error))
  } finally {
    exporting.value = false
  }
}

// ---------------------------------------------------------------- 日志抽屉（共用 JobLogDrawer，A4）
/** 打开某任务的作业日志：运行中直接用 `running_job_id`；已结束任务回查最近一条作业
 *  （E34 契约不含 ``last_job_id``，故经 E13 历史查询取 1 条——零后端改动）。 */
const logShow = ref(false)
const logJobId = ref('')
const logSkipped = ref(0)

function openLog(jobId: string, skipped = 0): void {
  logJobId.value = jobId
  logSkipped.value = skipped
  logShow.value = true
}

async function openTaskLog(taskId: number): Promise<void> {
  const task = tasks.value.find((item) => item.id === taskId) ?? null
  if (!task) {
    return
  }
  if (task.running_job_id) {
    openLog(task.running_job_id)
    return
  }
  try {
    const page = await listJobsHistory({ taskId, limit: 1 })
    const latest = page.items[0]
    if (!latest) {
      message.warning(t('task.noLogYet'))
      return
    }
    openLog(latest.id, latest.skipped_count)
  } catch (error) {
    // C9：不 rethrow（调用点多在 `void` 上下文，抛出即未处理 rejection）
    message.error(toText(error))
  }
}

// ---------------------------------------------------------------- 删除（三重确认门，keyword=任务名）
const deleteTarget = ref<TaskCardModel | null>(null)
const deleteGateShow = ref(false)
const deleting = ref(false)

function askDelete(taskId: number): void {
  const target = tasks.value.find((task) => task.id === taskId) ?? null
  if (!target) {
    return
  }
  deleteTarget.value = target
  deleteGateShow.value = true
}

async function onDeleteConfirmed(): Promise<void> {
  const target = deleteTarget.value
  if (!target) {
    return
  }
  deleting.value = true
  try {
    await deleteBackupTask(target.id)
    message.success(t('delete.done'))
    deleteGateShow.value = false
    deleteTarget.value = null
    await invalidateTasks()
  } catch (error) {
    // 运行中任务菜单项已置灰；此处兜底服务端 409 TASK_RUNNING（双保险）
    if (hasCode(error, 'TASK_RUNNING')) {
      message.error(t('delete.running_blocked'))
      return
    }
    // C9（owner 2026-10-23 裁决）：不再 rethrow —— 调用点多为 `void onXxx()`，
    // 抛出会变成 unhandled rejection；统一落成用户可见的错误提示。
    message.error(toText(error))
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <n-space vertical :size="16">
    <!-- D2 全局状态条（P7 ②裁决：全局进度 + Stop + 限速只读；砍 pause/resume 与运行中限速入口）。
         仅在进行中作业存在时渲染（GlobalStatusBar 内部 v-if=jobId），无作业时整条不占位。 -->
    <GlobalStatusBar
      v-if="globalJobId"
      :job-id="globalJobId"
      :title="globalTitle"
      :limit-text="globalLimitText"
      @stop="onStopGlobal"
      @abort="onAbortGlobal"
    />

    <!-- KPI 状态带（P4+）：四格等高等距——统一「标签行 22px / 主行 36px / 辅出行 18px」固定骨架，
         窄屏 2×2。任务卡虚拟滚动常量（CARD_HEIGHT 260 / ITEM_HEIGHT 272）不动。 -->
    <!-- 响应式断点只能用命名档（s/m/l/xl/xxl）：naive 的 responsive="screen" 查询的是断点布尔集，
         数字形式（原 "2 900:4"）永不命中 → 一直停在基础值 2 列（P4 实测修正）。 -->
    <n-grid cols="2 l:4" :x-gap="12" :y-gap="12" responsive="screen">
      <!-- ① 仓库状态：L3 三态（success/warning/error）标签为主行，容量降为辅出行 -->
      <n-gi>
        <n-card size="small" class="kpi">
          <div class="kpi__row kpi__row--label">
            <n-text depth="3" class="kpi__label">{{ t('dashboard.repoCard') }}</n-text>
            <n-button size="tiny" secondary :loading="repoLoading" @click="repoQuery.refetch()">
              {{ t('common.refresh') }}
            </n-button>
          </div>
          <div class="kpi__row kpi__row--main">
            <n-space align="center" :size="8">
              <StatusTag
                :kind="repoStatus?.connected ? 'success' : repoStatus?.configured === false ? 'warning' : 'error'"
                :label="
                  repoStatus?.connected
                    ? t('dashboard.repoConnected')
                    : repoStatus?.configured === false
                      ? t('dashboard.repoNotConfigured')
                      : t('dashboard.repoDisconnected')
                "
              />
              <StatusTag
                :kind="repoStatus?.locked ? 'warning' : 'default'"
                :label="repoStatus?.locked ? t('dashboard.repoLocked') : t('dashboard.repoUnlocked')"
              />
            </n-space>
          </div>
          <div class="kpi__row kpi__row--sub">
            <n-text depth="3" class="kpi__sub">{{ t('dashboard.repoCapacity') }} {{ capacityText }}</n-text>
          </div>
        </n-card>
      </n-gi>

      <!-- ② 快照数：count-up（AB-2 一次性） -->
      <n-gi>
        <n-card size="small" class="kpi">
          <div class="kpi__row kpi__row--label">
            <n-text depth="3" class="kpi__label">{{ t('dashboard.repoSnapshots') }}</n-text>
          </div>
          <div class="kpi__row kpi__row--main">
            <n-text class="kpi__num tabular-nums">{{ snapsText }}</n-text>
          </div>
          <div class="kpi__row kpi__row--sub">
            <n-text depth="3" class="kpi__sub">{{ t('dashboard.repoCacheNote') }}</n-text>
          </div>
        </n-card>
      </n-gi>

      <!-- ③ 运行模式（原 TierCard 内容并入；组件保留给任务向导） -->
      <n-gi>
        <n-card size="small" class="kpi">
          <div class="kpi__row kpi__row--label">
            <n-text depth="3" class="kpi__label">{{ t('tier.title') }}</n-text>
          </div>
          <div class="kpi__row kpi__row--main">
            <n-text strong class="kpi__tier">{{ t(`tier.name.${userKey}`) }}</n-text>
          </div>
          <div class="kpi__row kpi__row--sub">
            <n-text depth="3" class="kpi__sub">{{ t(`tier.user.${userKey}`) }}</n-text>
          </div>
        </n-card>
      </n-gi>

      <!-- ④ 最近备份：任务列表派生（零后端改动） -->
      <n-gi>
        <n-card size="small" class="kpi">
          <div class="kpi__row kpi__row--label">
            <n-text depth="3" class="kpi__label">{{ t('dashboard.lastBackup') }}</n-text>
          </div>
          <div class="kpi__row kpi__row--main">
            <n-text strong class="kpi__datetime tabular-nums">{{ lastBackupText }}</n-text>
          </div>
          <div class="kpi__row kpi__row--sub">
            <n-text depth="3" class="kpi__sub">{{ t('dashboard.tasksAutoNote') }}</n-text>
          </div>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- D1 双行动卡行（P7：Duplicati 主页 Backups/Restores 对位；整卡可点 + Enter/Space） -->
    <n-grid cols="1 l:2" :x-gap="12" :y-gap="12" responsive="screen" class="home__actions">
      <n-gi>
        <ActionCard
          :icon="IconPlusCircle"
          :title="t('dashboard.actions.newTask')"
          :description="t('dashboard.actions.newTaskDesc')"
          @click="goNewTask"
        />
      </n-gi>
      <n-gi>
        <ActionCard
          :icon="IconFolderOpen"
          :title="t('dashboard.actions.restore')"
          :description="t('dashboard.actions.restoreDesc')"
          @click="goRestore"
        />
      </n-gi>
    </n-grid>

    <!-- D3 工具栏：视图切换（localStorage 持久化）+ 时间口径（D4）+ 排序（D5，仅 list） -->
    <n-space :size="12" justify="space-between" align="center" class="home__toolbar">
      <SegmentedTabs
        :items="viewItems"
        v-model="viewMode"
        :aria-label="t('dashboard.view.aria')"
      />
      <!-- 右侧组刻意用原生 flex 而非 n-space：naive Space 展平插槽子节点时按索引发 key，
           组内两个 v-if（排序文案 + 排序下拉）会让子节点数在视图切换时 3↔1 抖动，
           展平索引相撞 → [Vue warn] Duplicate keys found during update: 1。 -->
      <div class="home__toolbar-right">
        <SegmentedTabs
          :items="timeItems"
          v-model="timeType"
          size="small"
          :aria-label="t('dashboard.time.aria')"
        />
        <n-text v-if="viewMode === 'list'" depth="3" class="home__sort-label">{{ t('dashboard.sort.label') }}</n-text>
        <n-select
          v-if="viewMode === 'list'"
          v-model:value="sortKey"
          :options="sortOptions"
          size="small"
          :placeholder="t('dashboard.sort.label')"
          style="width: 140px"
        />
      </div>
    </n-space>

    <!-- 运行告警行（原状态卡内 alerts 上提为整宽） -->
    <n-space vertical :size="8">
      <n-alert
        v-if="repoStatus?.last_error"
        :type="repoStatus.configured === false ? 'warning' : 'error'"
        :show-icon="false"
      >
        {{ t('dashboard.repoLastError') }}：{{ repoStatus.last_error }}
      </n-alert>
      <n-alert v-if="repoError" type="error" :show-icon="false">
        {{ t('dashboard.loadFailed', { message: repoError }) }}
      </n-alert>
    </n-space>

    <!-- 备份任务列表 / 详情（主内容，D3 双视图） -->
    <n-card :title="t('task.card.title')" size="small">
      <n-alert v-if="tasksError" type="error" :show-icon="false">
        {{ t('dashboard.loadFailed', { message: tasksError }) }}
      </n-alert>

      <!-- 空态：新建首个任务引导卡 -->
      <div v-else-if="sortedTasks.length === 0" class="tasks__empty">
        <n-text strong class="tasks__empty-title">{{ t('task.empty.title') }}</n-text>
        <n-text depth="3" class="tasks__empty-desc">{{ t('task.empty.desc') }}</n-text>
        <n-button type="primary" @click="goNewTask">{{ t('task.new') }}</n-button>
      </div>

      <!-- list 视图：虚拟滚动行卡（§4.5.6：TaskRowCard 即 list 视图卡片行；D5 排序后） -->
      <div
        v-else-if="viewMode === 'list'"
        v-bind="containerProps"
        class="tasks__viewport"
        :style="{ height: `${viewportHeight}px` }"
      >
        <div v-bind="wrapperProps">
          <div v-for="item in virtualItems" :key="item.data.id" class="tasks__item">
            <TaskRowCard
              :task="item.data"
              :time-mode="timeType"
              @start="onStart"
              @cancel="onCancel"
              @edit="onEdit"
              @delete="askDelete"
              @restore="goRestore"
              @verify="onVerify"
              @export="askExport"
              @log="openTaskLog"
            />
          </div>
        </div>
      </div>

      <!-- details 视图：表格（D3，含耗时/快照列；D4 时间口径共享） -->
      <n-data-table
        v-else
        :columns="detailColumns"
        :data="sortedTasks"
        :row-key="detailRowKey"
        size="small"
        :scroll-x="920"
        :virtual-scroll="sortedTasks.length > 50"
        :max-height="560"
      />

      <n-text depth="3" class="dashboard__note">{{ t('dashboard.tasksAutoNote') }}</n-text>
    </n-card>
  </n-space>

  <!-- 删除任务三重确认门：影响告知 → 输入任务名 → 红色确认 -->
  <ConfirmGate
    v-model:show="deleteGateShow"
    :title="t('delete.title')"
    :keyword="deleteTarget?.name ?? ''"
    :description="t('delete.impact')"
    :confirm-label="t('delete.confirmBtn')"
    :loading="deleting"
    @confirm="onDeleteConfirmed"
  >
    <template #preview>
      <n-space vertical :size="4">
        <n-text>{{ t('delete.type_name') }}：<n-text strong>{{ deleteTarget?.name }}</n-text></n-text>
        <n-text depth="3">{{ t('delete.final') }}</n-text>
      </n-space>
    </template>
  </ConfirmGate>

  <!-- Export 口令弹窗（P7 菜单 Export；口令 ≥10 位与后端 ConfigExportRequest 同口径） -->
  <n-modal
    v-model:show="exportShow"
    preset="card"
    :title="t('task.exportTitle')"
    style="width: 460px"
    @after-leave="closeExport"
  >
    <n-space vertical :size="12">
      <n-text depth="3">{{ t('task.exportHint', { name: exportTarget?.name ?? '' }) }}</n-text>
      <n-input
        v-model:value="exportPassword"
        type="password"
        show-password-on="click"
        :placeholder="t('task.exportPassword')"
        :maxlength="256"
        @keyup.enter="onExportConfirmed"
      />
      <n-text v-if="exportPassword.length > 0 && exportPassword.length < 10" depth="3" type="warning">
        {{ t('task.exportPasswordTooShort') }}
      </n-text>
      <n-space justify="end" :size="8">
        <n-button size="small" @click="closeExport">{{ t('common.cancel') }}</n-button>
        <n-button size="small" type="primary" :disabled="exportDisabled" :loading="exporting" @click="onExportConfirmed">
          {{ t('task.exportBtn') }}
        </n-button>
      </n-space>
    </n-space>
  </n-modal>

  <!-- 日志抽屉（与 jobs 页共用 JobLogDrawer；已结束作业同样可看历史日志） -->
  <JobLogDrawer v-model:show="logShow" :job-id="logJobId" :skipped-count="logSkipped" />
</template>

<style scoped>
/* KPI 状态带统一骨架（P4+）：四卡等高等距。
   三行固定行高——标签 22（容纳 tiny 刷新按钮）/ 主 36 / 辅出 18——
   卡片内边距四卡一致，叠加后总高相同；主行内按内容分档：
   数字 28px（②）、模式名 24px（③）、时间 20px（④）、状态标签自然高（①）。 */
.kpi__row {
  display: flex;
  align-items: center;
}

.kpi__row--label {
  height: 22px;
  justify-content: space-between;
}

.kpi__row--main {
  height: 36px;
}

.kpi__row--sub {
  height: 18px;
}

/* 标签 12px 辅助 / 主值分档 / 辅出 12px（方案 §3.1 字号阶梯） */
.kpi__label {
  font-size: 12px;
}

.kpi__num {
  font-size: 28px;
  font-weight: 600;
  line-height: 1.2;
}

.kpi__tier {
  font-size: 24px;
  font-weight: 600;
  line-height: 1.2;
}

/* 时间串 19 字符（YYYY-MM-DD HH:mm:ss）：20px + nowrap 省略号防溢出 */
.kpi__datetime {
  max-width: 100%;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kpi__sub {
  font-size: 12px;
}

.dashboard__note {
  display: block;
  margin-top: 10px;
  font-size: 12px;
}

/* D3 工具栏：两端对齐；窄屏断点内可换行（默认不换行避免移动端挤压） */
.home__toolbar {
  flex-wrap: wrap;
}

/* 工具栏右侧组（时间口径 + 排序）：替代原 n-space，见模板处注释（避免 duplicate key 告警）。 */
.home__toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.home__sort-label {
  font-size: 12px;
}

/* useVirtualList 容器：overflowY 由 containerProps 提供，这里只补白与圆角 */
.tasks__viewport {
  border-radius: 3px;
}

/* 固定行卡骨架：ITEM_HEIGHT = ROW_HEIGHT(84) + ROW_GAP(8) = 92，间距用 padding-bottom 实现 */
.tasks__item {
  height: 92px;
  padding-bottom: 8px;
  box-sizing: border-box;
}

/* 空态引导卡：垂直居中，标题/说明/主按钮 */
.tasks__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 36px 16px;
  border: 1px dashed rgba(128, 128, 128, 0.32);
  border-radius: 3px;
}

.tasks__empty-title {
  font-size: 14px;
}

.tasks__empty-desc {
  font-size: 12px;
  text-align: center;
  max-width: 420px;
}
</style>
