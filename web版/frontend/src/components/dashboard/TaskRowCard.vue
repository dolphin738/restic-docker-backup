<script setup lang="ts">
/** 行卡片（P6 L2 共用形态层 → P7 list 视图任务行，方案 §4.5.6:292）。
 *
 * 形态来源：Duplicati home list 视图备份行（ngclient home：名称 + chip / 指标区 / 操作区）。
 * 方案原文职责：「list 视图卡片行（名称 + 类型 chip + 快照数 chip / 指标区 / 操作区）」。
 *
 * 结构（固定行高，供主页虚拟滚动按常量切片，见 dashboard `ROW_HEIGHT`）：
 *   ① 名称行：任务名 + 状态徽章 + 「7 天未跑」/ prune 提示 + 快照数 chip
 *   ② 指标区：上次运行 / 下次运行 / 耗时 / 上次结果（时间口径随 timeMode）
 *   ③ 操作区：Start｜Cancel（运行中）+ ⋯ 菜单（编辑/恢复/校验/导出/日志/删除）
 *
 * 与旧 `TaskCard`（已按 §4.5.6 退役）的差异**如实标注**：行内不再嵌 `JobProgress`
 * （那会为同一作业开第二条 SSE；运行中全局进度由 D2 状态条承担，行内只留状态徽章 +
 * Cancel/日志入口）。该差异登记在方案 §4.5.2 D2 备注。
 */

import { NButton, NSpace, NTag, NText, NTooltip } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TaskCard as TaskCardModel } from '@/api/types'
import StatusTag from '@/components/StatusTag.vue'
import TaskActionsMenu from '@/components/dashboard/TaskActionsMenu.vue'
import { formatDurationMs, statusKind, timeText } from '@/utils/taskFormat'

const props = defineProps<{
  /** E34 任务卡片数据（与 details 表格同一份契约）。 */
  task: TaskCardModel
  /** 时间展示口径：actual=绝对时间，relative=相对时间（P7 D4，由工具栏驱动）。 */
  timeMode?: 'actual' | 'relative'
}>()

const emit = defineEmits<{
  (e: 'start', taskId: number): void
  (e: 'cancel', jobId: string): void
  (e: 'edit', taskId: number): void
  (e: 'delete', taskId: number): void
  (e: 'restore', taskId: number): void
  (e: 'verify', taskId: number): void
  (e: 'export', taskId: number): void
  (e: 'log', taskId: number): void
}>()

const { t } = useI18n()

const kind = computed(() => statusKind(props.task.status))
const isActive = computed(() => props.task.status === 'running' || props.task.status === 'pending')
const runningJobId = computed(() => props.task.running_job_id)
/** dry-run 门禁（R-026-08）：未预览过扫描范围时拦截 Start。 */
const startBlocked = computed(() => props.task.dry_run_pending)

/** P4「7 天未跑」提示：启用中任务超 7 天无运行记录（never_run 由状态徽章表达，不重复）。 */
const stale = computed(() => {
  if (!props.task.enabled || !props.task.last_run_at) {
    return false
  }
  return Date.now() - new Date(props.task.last_run_at).getTime() > 7 * 24 * 60 * 60 * 1000
})

const lastRunText = computed(() =>
  props.timeMode === 'relative'
    ? timeText(props.task.last_run_at, 'relative', t, t('common.none'))
    : timeText(props.task.last_run_at, 'actual', t, t('common.none')),
)
const nextRunText = computed(() => {
  if (!props.task.enabled) {
    return t('task.paused')
  }
  const mode = props.timeMode === 'relative' ? 'relative' : 'actual'
  return timeText(props.task.next_run_at, mode, t, t('task.manual_only'))
})

/** D7：版本数 chip——该任务快照数（null=不可知时不渲染，绝不退化成 0）。 */
const snapshotLabel = computed(() =>
  props.task.snapshot_count === null ? '' : t('task.snapshotCount', { n: props.task.snapshot_count }),
)

/** D6：耗时——null 不渲染（不显示占位破折号，行卡空间有限）。 */
const durationText = computed(() => {
  const ms = props.task.last_duration_ms
  return ms === null || ms < 0 ? '' : formatDurationMs(ms)
})

const lastResultText = computed(() => {
  const result = props.task.last_result
  if (!result) {
    return ''
  }
  const key = `task.status.${result}`
  const text = t(key)
  return text === key ? result : text
})

/** 三点菜单（§4.5.6：Operations / Advanced / Reporting 三组 + Danger）。
 *  P7 菜单清单 Restore / Export / Delete / Verify / Log 全部在此落地。 */
const menuGroups = computed(() => [
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
    actions: [{ key: 'delete', label: t('task.delete'), disabled: isActive.value }],
  },
])

function onMenuSelect(key: string): void {
  if (key === 'edit') {
    emit('edit', props.task.id)
  } else if (key === 'delete') {
    emit('delete', props.task.id)
  } else if (key === 'restore') {
    emit('restore', props.task.id)
  } else if (key === 'verify') {
    emit('verify', props.task.id)
  } else if (key === 'export') {
    emit('export', props.task.id)
  } else if (key === 'log') {
    emit('log', props.task.id)
  }
}
</script>

<template>
  <div class="rw-row-card task-row" :class="{ 'task-row--active': isActive }">
    <div class="rw-row-card__body">
      <!-- ① 名称行 -->
      <div class="rw-row-card__name">
        <n-text strong class="task-row__name">{{ task.name }}</n-text>
        <StatusTag :kind="kind" :label="t(`task.status.${task.status}`)" />
        <StatusTag v-if="stale" kind="warning" :label="t('task.stale7d')" />
        <n-tooltip v-if="task.prune_due" trigger="hover">
          <template #trigger>
            <StatusTag kind="warning" :label="t('task.pruneDue')" />
          </template>
          {{ t('task.pruneDueNote') }}
        </n-tooltip>
        <n-tag v-if="snapshotLabel" size="small" :bordered="false" type="default">{{ snapshotLabel }}</n-tag>
      </div>

      <!-- ② 指标区 -->
      <n-space :size="16" :wrap="false" class="task-row__metrics">
        <n-text depth="3">{{ t('task.last_run_label') }}<n-text depth="2">{{ lastRunText }}</n-text></n-text>
        <n-text depth="3">{{ t('task.next_run') }}：<n-text depth="2">{{ nextRunText }}</n-text></n-text>
        <n-text v-if="durationText" depth="3">{{ t('task.duration') }}：<n-text depth="2">{{ durationText }}</n-text></n-text>
        <n-space v-if="lastResultText" align="center" :size="4">
          <n-text depth="3">{{ t('task.last_result_label') }}</n-text>
          <StatusTag :kind="kind" :label="lastResultText" />
        </n-space>
      </n-space>
    </div>

    <!-- ③ 操作区 -->
    <div class="rw-row-card__actions">
      <n-space :size="8" :wrap="false" align="center">
        <n-tooltip v-if="startBlocked && !isActive" trigger="hover">
          <template #trigger>
            <n-button size="small" type="primary" disabled>{{ t('task.start') }}</n-button>
          </template>
          {{ t('wizard.rescan_required') }}
        </n-tooltip>
        <n-button v-else-if="!isActive" size="small" type="primary" @click="emit('start', task.id)">
          {{ t('task.start') }}
        </n-button>
        <n-button v-if="isActive && runningJobId" size="small" @click="emit('cancel', runningJobId)">
          {{ t('task.cancel') }}
        </n-button>
        <n-button v-if="runningJobId" size="small" quaternary @click="emit('log', task.id)">
          {{ t('task.logs') }}
        </n-button>
        <TaskActionsMenu :groups="menuGroups" :aria-label="t('task.menu')" @select="onMenuSelect" />
      </n-space>
    </div>
  </div>
</template>

<style scoped>
/* 行卡改为「任务行」：无选中语义 → 去掉可点手型与外层点击反馈（形态类仍在 skin.css） */
.task-row {
  cursor: default;
  height: 100%;
  box-sizing: border-box;
}

/* 运行中行：左侧强调条，替代旧卡片的内联进度条（进度由 D2 全局状态条承担） */
.task-row--active {
  border-color: var(--rw-focus);
}

.task-row__name {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-row__metrics {
  flex-wrap: wrap;
  font-size: 12px;
}
</style>
