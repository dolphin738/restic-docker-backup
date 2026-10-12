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
  NStatistic,
  NTag,
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
import TaskCard from '@/components/TaskCard.vue'
import TierCard from '@/components/TierCard.vue'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const router = useRouter()
const ui = useUiStore()
const queryClient = useQueryClient()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

// E30 服务端 30s 缓存：这里按 60s 轮询 + 手动刷新，禁止秒级轮询
const repoQuery = useQuery({
  queryKey: ['repo', 'status'],
  queryFn: getRepoStatus,
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

const capacityText = computed(() =>
  repoStatus.value?.capacity.size ? formatBytes(repoStatus.value.capacity.size) : t('common.none'),
)
const snapsText = computed(() =>
  repoStatus.value?.capacity.snaps === null || repoStatus.value?.capacity.snaps === undefined
    ? t('common.none')
    : String(repoStatus.value.capacity.snaps),
)

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
    <n-grid cols="1 900:2" :x-gap="16" :y-gap="16" responsive="screen">
      <!-- 仓库状态卡 -->
      <n-gi>
        <n-card :title="t('dashboard.repoCard')" size="small">
          <template #header-extra>
            <n-button size="tiny" secondary :loading="repoLoading" @click="repoQuery.refetch()">
              {{ t('common.refresh') }}
            </n-button>
          </template>

          <n-space vertical :size="10">
            <n-space align="center" :size="8">
              <n-tag size="small" round :type="repoStatus?.connected ? 'success' : 'error'">
                {{ repoStatus?.connected ? t('dashboard.repoConnected') : t('dashboard.repoDisconnected') }}
              </n-tag>
              <n-tag size="small" round :type="repoStatus?.locked ? 'warning' : 'default'">
                {{ repoStatus?.locked ? t('dashboard.repoLocked') : t('dashboard.repoUnlocked') }}
              </n-tag>
            </n-space>

            <n-space :size="24">
              <n-statistic :label="t('dashboard.repoCapacity')" :value="capacityText" />
              <n-statistic :label="t('dashboard.repoSnapshots')" :value="snapsText" />
            </n-space>

            <n-alert v-if="repoStatus?.last_error" type="error" :show-icon="false">
              {{ t('dashboard.repoLastError') }}：{{ repoStatus.last_error }}
            </n-alert>

            <n-alert v-if="repoError" type="error" :show-icon="false">
              {{ t('dashboard.loadFailed', { message: repoError }) }}
            </n-alert>

            <n-text depth="3" class="dashboard__note">{{ t('dashboard.repoCacheNote') }}</n-text>
          </n-space>
        </n-card>
      </n-gi>

      <!-- 运行模式卡：运行用户 × /host 挂载模式，只展示当前模式 -->
      <n-gi>
        <TierCard :user="ui.me?.user ?? ''" :host-mount="ui.me?.host_mount ?? 'ro'" />
      </n-gi>
    </n-grid>

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
