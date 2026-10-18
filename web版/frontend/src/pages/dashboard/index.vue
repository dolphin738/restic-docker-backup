<script setup lang="ts">
/** 仪表盘（增量设计 §5.1）：仓库状态卡 + 运行模式卡保留；
 * 原「进行中任务卡」退役——进行中 job 并入任务卡片内联进度；
 * 新增备份任务卡片列表（useVirtualList，固定卡片骨架，Q6/N2 裁决）。
 */

import { useVirtualList } from '@vueuse/core'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NGi,
  NGrid,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { deleteBackupTask, listBackupTasks } from '@/api/backupTasks'
import { hasCode, isAppError } from '@/api/http'
import { cancelJob, createJob } from '@/api/jobs'
import { getRepoStatus } from '@/api/repo'
import type { TaskCard as TaskCardModel } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import StatusTag from '@/components/StatusTag.vue'
import TaskCard from '@/components/TaskCard.vue'
import { useCountUp } from '@/composables/useCountUp'
import { useUiStore } from '@/stores/ui'
import { formatDateTimeOr } from '@/utils/datetime'

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
  // 时间戳取 Date.parse 数值比较（审查 💭②）：不依赖 ISO 字符串字典序与后端格式完全统一的隐含约定
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

// ---------------------------------------------------------------- 虚拟滚动（固定卡片骨架）
const CARD_HEIGHT = 260
const CARD_GAP = 12
const ITEM_HEIGHT = CARD_HEIGHT + CARD_GAP
const MAX_VISIBLE = 3

const { containerProps, wrapperProps, list: virtualItems } = useVirtualList(tasks, {
  itemHeight: ITEM_HEIGHT,
})

/** 列表较短时不出现内层滚动条：容器高度随条数伸缩（最多 3 个卡位）。 */
const viewportHeight = computed(() => Math.max(ITEM_HEIGHT, Math.min(tasks.value.length, MAX_VISIBLE) * ITEM_HEIGHT))

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
    <!-- KPI 状态带（P4）：原「仓库状态卡 + 运行模式卡 + 2×NStatistic」三卡合一，
         四格（仓库状态 / 快照数 / 运行模式 / 最近备份），窄屏 2×2。
         任务卡虚拟滚动常量（CARD_HEIGHT 260 / ITEM_HEIGHT 272）不动。 -->
    <!-- 响应式断点只能用命名档（s/m/l/xl/xxl）：naive 的 responsive="screen" 查询的是断点布尔集，
         数字形式（原 "2 900:4"）永不命中 → 一直停在基础值 2 列（P4 实测修正）。 -->
    <n-grid cols="2 l:4" :x-gap="12" :y-gap="12" responsive="screen">
      <!-- ① 仓库状态：L3 三态（success/warning/error）+ 容量 -->
      <n-gi>
        <n-card size="small" class="kpi">
          <n-space justify="space-between" align="center">
            <n-text depth="3" class="kpi__label">{{ t('dashboard.repoCard') }}</n-text>
            <n-button size="tiny" secondary :loading="repoLoading" @click="repoQuery.refetch()">
              {{ t('common.refresh') }}
            </n-button>
          </n-space>
          <n-space align="center" :size="8" class="kpi__tags">
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
          <n-text depth="2" class="kpi__value tabular-nums">{{ capacityText }}</n-text>
          <n-text depth="3" class="kpi__sub">{{ t('dashboard.repoCapacity') }}</n-text>
        </n-card>
      </n-gi>

      <!-- ② 快照数：count-up（AB-2 一次性） -->
      <n-gi>
        <n-card size="small" class="kpi">
          <n-text depth="3" class="kpi__label">{{ t('dashboard.repoSnapshots') }}</n-text>
          <n-text class="kpi__num tabular-nums">{{ snapsText }}</n-text>
          <n-text depth="3" class="kpi__sub">{{ t('dashboard.repoCacheNote') }}</n-text>
        </n-card>
      </n-gi>

      <!-- ③ 运行模式（原 TierCard 内容并入；组件保留给任务向导） -->
      <n-gi>
        <n-card size="small" class="kpi">
          <n-text depth="3" class="kpi__label">{{ t('tier.title') }}</n-text>
          <n-text strong class="kpi__num">{{ t(`tier.name.${userKey}`) }}</n-text>
          <n-text depth="3" class="kpi__sub">{{ t(`tier.user.${userKey}`) }}</n-text>
        </n-card>
      </n-gi>

      <!-- ④ 最近备份：任务列表派生（零后端改动） -->
      <n-gi>
        <n-card size="small" class="kpi">
          <n-text depth="3" class="kpi__label">{{ t('dashboard.lastBackup') }}</n-text>
          <n-text strong class="kpi__value tabular-nums">{{ lastBackupText }}</n-text>
          <n-text depth="3" class="kpi__sub">{{ t('dashboard.tasksAutoNote') }}</n-text>
        </n-card>
      </n-gi>
    </n-grid>

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

    <!-- 备份任务卡片列表（主内容） -->
    <n-card :title="t('task.card.title')" size="small">
      <template #header-extra>
        <n-button size="small" type="primary" @click="goNewTask">{{ t('task.new') }}</n-button>
      </template>

      <n-alert v-if="tasksError" type="error" :show-icon="false">
        {{ t('dashboard.loadFailed', { message: tasksError }) }}
      </n-alert>

      <!-- 空态：新建首个任务引导卡 -->
      <div v-else-if="tasks.length === 0" class="tasks__empty">
        <n-text strong class="tasks__empty-title">{{ t('task.empty.title') }}</n-text>
        <n-text depth="3" class="tasks__empty-desc">{{ t('task.empty.desc') }}</n-text>
        <n-button type="primary" @click="goNewTask">{{ t('task.new') }}</n-button>
      </div>

      <!-- 虚拟滚动：固定卡片骨架，内容超高地截断/省略 -->
      <div v-else v-bind="containerProps" class="tasks__viewport" :style="{ height: `${viewportHeight}px` }">
        <div v-bind="wrapperProps">
          <div v-for="item in virtualItems" :key="item.data.id" class="tasks__item">
            <TaskCard
              :task="item.data"
              @start="onStart"
              @cancel="onCancel"
              @edit="onEdit"
              @delete="askDelete"
            />
          </div>
        </div>
      </div>

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
/* KPI 状态带单元格（P4）：标签 12px 辅助 / 主值 20px / 大数字 28px（方案 §3.1 字号阶梯） */
.kpi__label {
  font-size: 12px;
}

.kpi__tags {
  margin-top: 8px;
  min-height: 22px;
}

.kpi__value {
  display: block;
  margin-top: 8px;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.3;
}

.kpi__num {
  display: block;
  margin-top: 8px;
  font-size: 28px;
  font-weight: 600;
  line-height: 1.2;
}

.kpi__sub {
  display: block;
  margin-top: 4px;
  font-size: 12px;
}

.dashboard__note {
  display: block;
  margin-top: 10px;
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
