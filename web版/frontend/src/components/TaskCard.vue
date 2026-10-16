<script setup lang="ts">
/** 备份任务卡片（增量设计 §5.2 / UI 文档 §4.3：扁平卡片——白底/1px 边框/小圆角）。
 *
 * 结构：标题行（名称 + 状态徽章 + ⋯ 菜单：编辑/删除）→ 元信息行（上次/下次运行）
 * → 进度区（running 时嵌 JobProgress + 取消；终态时结果摘要）→ 操作行（立即运行 + 日志）。
 * 状态徽章色用 Naive 语义色；卡片高度由调用方（仪表盘虚拟列表）固定，内容超高地截断。
 */

import { NButton, NCard, NDropdown, NSpace, NTag, NText, NTooltip } from 'naive-ui'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TaskCard as TaskCardModel } from '@/api/types'
import JobProgress from '@/components/JobProgress.vue'
import { formatDateTimeOr } from '@/utils/datetime'

const props = defineProps<{
  task: TaskCardModel
}>()

const emit = defineEmits<{
  (e: 'start', taskId: number): void
  (e: 'cancel', jobId: string): void
  (e: 'edit', taskId: number): void
  (e: 'delete', taskId: number): void
}>()

const { t } = useI18n()

/** 徽章色：running/pending=info，success=success，failed=error，interrupted=warning，其余中性。 */
const statusTagType = computed<'default' | 'info' | 'success' | 'warning' | 'error'>(() => {
  switch (props.task.status) {
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
})

const isActive = computed(() => props.task.status === 'running' || props.task.status === 'pending')
const runningJobId = computed(() => props.task.running_job_id)

/** 上次运行：ISO 原始串收口为本地可读时间；无记录时回落「无」。 */
const lastRunText = computed(() => formatDateTimeOr(props.task.last_run_at, t('common.none')))

const nextRunText = computed(() => {
  if (!props.task.enabled) {
    return t('task.paused')
  }
  return formatDateTimeOr(props.task.next_run_at, t('task.manual_only'))
})

/** dry-run 门禁（R-026-08）：未预览过扫描范围时拦截 Start，工具提示引导去向导第 5 步。 */
const startBlocked = computed(() => props.task.dry_run_pending)

const lastResultText = computed(() => {
  const result = props.task.last_result
  if (!result) {
    return ''
  }
  const key = `task.status.${result}`
  const text = t(key)
  return text === key ? result : text
})

const menuOptions = computed(() => [
  { label: t('task.edit'), key: 'edit' },
  { label: t('task.delete'), key: 'delete', disabled: isActive.value },
])

function onMenuSelect(key: string | number): void {
  if (key === 'edit') {
    emit('edit', props.task.id)
    return
  }
  if (key === 'delete') {
    emit('delete', props.task.id)
  }
}

/** 内联 JobProgress 的日志抽屉由组件自身管理；操作行「日志」入口经 expose 打开同一抽屉。 */
const progressRef = ref<InstanceType<typeof JobProgress> | null>(null)

function openLogs(): void {
  progressRef.value?.openLog()
}
</script>

<template>
  <n-card class="task-card" size="small" :bordered="true" :style="{ height: '100%' }">
    <template #header>
      <n-space align="center" :size="8" class="task-card__header">
        <n-text strong class="task-card__name">{{ task.name }}</n-text>
        <n-tag size="small" round :type="statusTagType">{{ t(`task.status.${task.status}`) }}</n-tag>
        <n-tooltip v-if="task.prune_due" trigger="hover">
          <template #trigger>
            <n-tag size="small" round type="warning">{{ t('task.pruneDue') }}</n-tag>
          </template>
          {{ t('task.pruneDueNote') }}
        </n-tooltip>
      </n-space>
    </template>

    <template #header-extra>
      <n-dropdown trigger="click" :options="menuOptions" @select="onMenuSelect">
        <n-button size="tiny" quaternary :aria-label="t('task.menu')">⋯</n-button>
      </n-dropdown>
    </template>

    <n-space vertical :size="8" class="task-card__body">
      <!-- 元信息行：上次/下次运行 -->
      <n-space :size="16" class="task-card__meta">
        <n-text depth="3" class="task-card__meta-item">
          {{ t('task.last_run_label') }}<n-text depth="2">{{ lastRunText }}</n-text>
        </n-text>
        <n-text depth="3" class="task-card__meta-item">
          {{ t('task.next_run') }}：<n-text depth="2">{{ nextRunText }}</n-text>
        </n-text>
      </n-space>

      <!-- 进度区：running 时内联 SSE 进度；终态时结果摘要 -->
      <div class="task-card__progress">
        <JobProgress
          v-if="runningJobId"
          ref="progressRef"
          :job-id="runningJobId"
          :title="t('task.progress')"
        />
        <n-space v-else-if="lastResultText" align="center" :size="6">
          <!-- 缺陷 B：结果徽章用独立键 task.last_result_label，与「上次运行：」区分，避免同屏两个同义标签 -->
          <n-text depth="3">{{ t('task.last_result_label') }}</n-text>
          <n-tag size="small" round :type="statusTagType">{{ lastResultText }}</n-tag>
        </n-space>
      </div>

      <!-- 操作行 -->
      <n-space class="task-card__actions" :size="8">
        <n-tooltip v-if="startBlocked && !isActive" trigger="hover">
          <template #trigger>
            <n-button size="small" type="primary" disabled>{{ t('task.start') }}</n-button>
          </template>
          {{ t('wizard.rescan_required') }}
        </n-tooltip>
        <n-button
          v-else-if="!isActive"
          size="small"
          type="primary"
          @click="emit('start', task.id)"
        >
          {{ t('task.start') }}
        </n-button>
        <n-button v-if="isActive && runningJobId" size="small" @click="emit('cancel', runningJobId)">
          {{ t('task.cancel') }}
        </n-button>
        <n-button v-if="runningJobId" size="small" quaternary @click="openLogs">
          {{ t('task.logs') }}
        </n-button>
      </n-space>
    </n-space>
  </n-card>
</template>

<style scoped>
.task-card {
  /* 扁平卡片（UI 文档 §4.3）：令牌继承下的 1px 边框 + 小圆角，无阴影 */
  border-radius: 3px;
}

.task-card :deep(.n-card__content) {
  height: 100%;
  padding: 12px 16px;
  box-sizing: border-box;
}

.task-card__header {
  min-width: 0;
}

.task-card__name {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-card__body {
  height: 100%;
}

.task-card__meta {
  flex-wrap: wrap;
}

.task-card__meta-item {
  font-size: 12px;
}

.task-card__progress {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}

.task-card__actions {
  flex-wrap: wrap;
}
</style>
