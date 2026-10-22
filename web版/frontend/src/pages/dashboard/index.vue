<script setup lang="ts">
/** 仪表盘（增量设计 §5.1 + P7 主页复刻 §4.5）：仓库状态卡 + 运行模式卡保留；
 * 新增：双行动卡行（D1）/ 全局状态条（D2）/ 工具栏（D3 视图切换 + localStorage、
 * D4 相对/绝对时间、D5 纯前端排序）/ list-details 双视图。
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
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, h, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { deleteBackupTask, listBackupTasks } from '@/api/backupTasks'
import { hasCode, isAppError } from '@/api/http'
import { cancelJob, createJob } from '@/api/jobs'
import { getRepoStatus } from '@/api/repo'
import type { TaskCard as TaskCardModel } from '@/api/types'
import ActionCard from '@/components/dashboard/ActionCard.vue'
import GlobalStatusBar from '@/components/dashboard/GlobalStatusBar.vue'
import SegmentedTabs from '@/components/dashboard/SegmentedTabs.vue'
import ConfirmGate from '@/components/ConfirmGate.vue'
import StatusTag from '@/components/StatusTag.vue'
import TaskCard from '@/components/TaskCard.vue'
import { useCountUp } from '@/composables/useCountUp'
import { useUiStore } from '@/stores/ui'
import { formatDateTime, formatDateTimeOr } from '@/utils/datetime'
import { relativeTime } from '@/utils/relativeTime'
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

/** 表格时间列收口（D4 口径）：actual=绝对；relative=相对，回落绝对/语义兜底。 */
function tableTime(value: string | null, fallback: string): string {
  if (!value) {
    return fallback
  }
  if (timeType.value === 'actual') {
    return formatDateTimeOr(value, fallback)
  }
  const r = relativeTime(value)
  if (r.kind === 'absolute') {
    return formatDateTime(r.date)
  }
  if (r.kind === 'none') {
    return formatDateTimeOr(value, fallback)
  }
  if (r.kind === 'justNow') {
    return t('dashboard.rel.justNow')
  }
  if (r.kind === 'soon') {
    return t('dashboard.rel.soon')
  }
  const unit = r.unit === 'min' ? t('dashboard.rel.min') : r.unit === 'hour' ? t('dashboard.rel.hour') : t('dashboard.rel.day')
  const dir = r.future ? t('dashboard.rel.later') : t('dashboard.rel.ago')
  return `${r.amount}${unit}${dir}`
}

/** 耗时（ms）→ 紧凑展示；null 回落长破折号。 */
function formatDuration(ms: number | null): string {
  if (ms === null || ms < 0) {
    return '—'
  }
  const totalSec = Math.floor(ms / 1000)
  const h = Math.floor(totalSec / 3600)
  const m = Math.floor((totalSec % 3600) / 60)
  const s = totalSec % 60
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }
  return `${m}:${String(s).padStart(2, '0')}`
}

/** 状态徽章语义色（与 TaskCard.statusTagType 同口径，表格列复用）。 */
function statusKind(status: string): 'default' | 'info' | 'success' | 'warning' | 'error' {
  switch (status) {
    case 'running':
    case 'pending':
      return 'info'
    case 'success':
      return 'success'
    case 'failed':
      return 'error'
    case 'interrupted':
      return 'warning'
    default:
      return 'default'
  }
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
    render: (row: TaskCardModel) => formatDuration(row.last_duration_ms),
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
    width: 200,
    render: (row: TaskCardModel) => renderRowActions(row),
  },
])

/** details 表格操作区（D8 之前先用按钮组，D8 接入 TaskActionsMenu 后再替换）。 */
function renderRowActions(row: TaskCardModel) {
  const isActive = row.status === 'running' || row.status === 'pending'
  const nodes: ReturnType<typeof h>[] = []
  if (isActive && row.running_job_id) {
    nodes.push(
      h(NButton, { size: 'small', onClick: () => void onCancel(row.running_job_id!) }, { default: () => t('task.cancel') }),
    )
  } else {
    nodes.push(
      h(
        NButton,
        { size: 'small', type: 'primary', disabled: row.dry_run_pending, onClick: () => void onStart(row.id) },
        { default: () => t('task.start') },
      ),
    )
  }
  nodes.push(h(NButton, { size: 'small', quaternary: true, onClick: () => onEdit(row.id) }, { default: () => t('task.edit') }))
  nodes.push(
    h(
      NButton,
      { size: 'small', quaternary: true, disabled: isActive, onClick: () => askDelete(row.id) },
      { default: () => t('task.delete') },
    ),
  )
  return h(NSpace, { size: 8 }, { default: () => nodes })
}

// ---------------------------------------------------------------- 虚拟滚动（固定卡片骨架，仅 list 视图）
const CARD_HEIGHT = 260
const CARD_GAP = 12
const ITEM_HEIGHT = CARD_HEIGHT + CARD_GAP
const MAX_VISIBLE = 3

const { containerProps, wrapperProps, list: virtualItems } = useVirtualList(sortedTasks, {
  itemHeight: ITEM_HEIGHT,
})

/** 列表较短时不出现内层滚动条：容器高度随条数伸缩（最多 3 个卡位）。 */
const viewportHeight = computed(() => Math.max(ITEM_HEIGHT, Math.min(sortedTasks.value.length, MAX_VISIBLE) * ITEM_HEIGHT))

function toText(error: unknown): string {
  if (!error) {
    return ''
  }
  return isAppError(error) ? error.message : t('errors.UNKNOWN')
}

function formatBytes(value: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
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
    if (!isAppError(error)) {
      throw error
    }
  }
}

async function onCancel(jobId: string): Promise<void> {
  try {
    await cancelJob(jobId)
    message.success(t('jobs.cancelSubmitted'))
    await invalidateTasks()
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
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
    if (!isAppError(error)) {
      throw error
    }
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
        :aria-label="t('dashboard.view.list')"
      />
      <n-space :size="8" :wrap="false" align="center">
        <SegmentedTabs
          :items="timeItems"
          v-model="timeType"
          size="small"
          :aria-label="t('dashboard.time.relative')"
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
      </n-space>
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

      <!-- list 视图：虚拟滚动卡片（D5 排序后） -->
      <div
        v-else-if="viewMode === 'list'"
        v-bind="containerProps"
        class="tasks__viewport"
        :style="{ height: `${viewportHeight}px` }"
      >
        <div v-bind="wrapperProps">
          <div v-for="item in virtualItems" :key="item.data.id" class="tasks__item">
            <TaskCard
              :task="item.data"
              :time-mode="timeType"
              @start="onStart"
              @cancel="onCancel"
              @edit="onEdit"
              @delete="askDelete"
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

.home__sort-label {
  font-size: 12px;
}

/* useVirtualList 容器：overflowY 由 containerProps 提供，这里只补白与圆角 */
.tasks__viewport {
  border-radius: 3px;
}

/* 固定卡片骨架：ITEM_HEIGHT = CARD_HEIGHT(260) + CARD_GAP(12) = 272，间距用 padding-bottom 实现 */
.tasks__item {
  height: 272px;
  padding-bottom: 12px;
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
