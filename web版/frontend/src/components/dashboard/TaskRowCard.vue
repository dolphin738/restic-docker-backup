<script setup lang="ts">
/** 行卡片（P6 L2 共用形态层 → P7 list 视图任务行，方案 §4.5.6:292）。
 *
 * 形态来源：Duplicati home list 视图备份行（ngclient home：名称 + chip / 指标区 / 操作区）。
 * 方案原文职责：「list 视图卡片行（名称 + 类型 chip + 快照数 chip / 指标区 / 操作区）」。
 *
 * 结构（固定行高，供主页虚拟滚动按常量切片，见 dashboard `ROW_HEIGHT`）：
 *   ① 名称行：任务名 + 「7 天未跑」/ prune 提示 + 快照数 chip
 *   ② 指标行：上次成功备份于 / 下一次排程执行 / 用时（横排，空值一律「—」）
 *      + **上次结果徽章（紧跟「用时」之后——原位置；不往行尾推，不靠按钮）**
 *   ③ 操作区：同形态三键——开始（播放图标 + 文案，primary）｜编辑（画笔）｜⋯ 菜单
 *      （运行中：开始换成 Cancel + 日志）
 *
 * 与旧 `TaskCard`（已按 §4.5.6 退役）的差异**如实标注**：行内不再嵌 `JobProgress`
 * （那会为同一作业开第二条 SSE；运行中全局进度由 D2 状态条承担，行内只留状态徽章 +
 * Cancel/日志入口）。该差异登记在方案 §4.5.2 D2 备注。
 */

import { NButton, NIcon, NTag, NText, NTooltip } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TaskCard as TaskCardModel } from '@/api/types'
import StatusTag from '@/components/StatusTag.vue'
import TaskActionsMenu from '@/components/dashboard/TaskActionsMenu.vue'
import {
  IconDownload,
  IconEdit,
  IconFile,
  IconPlay,
  IconRestore,
  IconShieldCheck,
  IconTrash,
} from '@/components/icons/icons'
import { formatDurationMs, statusKind, timeText } from '@/utils/taskFormat'

/** 空值占位：与 details 表格同一形态（dashboard `tableDuration` 的「—」），不写 0、不写「无」。 */
const EMPTY_VALUE = '—'

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
/**
 * 上次备份的**标签**：只有「确实成功过」才称「上次成功备份于」——
 * last_result 为 failed/interrupted/cancelled 时退回「上次运行」（不能把一次失败的尝试说成成功备份）。
 * 从未运行（last_result 为 null）时用成功口径 + 「—」：此时确实没有成功备份，语义成立。
 */
const lastBackupLabel = computed(() =>
  props.task.last_result && props.task.last_result !== 'success'
    ? t('task.last_run_label')
    : t('task.last_success_label'),
)
const lastBackupValue = computed(() =>
  props.task.last_result === null ? EMPTY_VALUE : lastRunText.value,
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

/** D6：用时——null 也渲染，占位「—」（对齐 Duplicati 行：指标位恒定，不因空值跳动）。 */
const durationText = computed(() => {
  const ms = props.task.last_duration_ms
  return ms === null || ms < 0 ? EMPTY_VALUE : formatDurationMs(ms) || EMPTY_VALUE
})

/** 上次结果徽章文案：`last_result` 走**短状态词**（成功/失败/中断/已取消），
 *  长词（上次成功）会与同一行的「上次成功备份于」撞成两个「上次成功」（owner 2026-10-24 裁决）。 */
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
 *  P7 菜单清单 Restore / Export / Delete / Verify / Log 全部在此落地。
 *  编辑已提到行内画笔按钮（对齐 Duplicati 行：常用操作不藏进菜单），故菜单不再含 edit。
 *  条目图标：与 Duplicati 菜单一致（左图标 + 文案），图标取 icons.ts 既有资产。 */
const menuGroups = computed(() => [
  {
    actions: [
      { key: 'restore', label: t('task.restore'), icon: IconRestore },
      { key: 'export', label: t('task.export'), icon: IconDownload },
    ],
  },
  {
    label: t('task.groupAdvanced'),
    actions: [{ key: 'verify', label: t('task.verify'), icon: IconShieldCheck }],
  },
  {
    label: t('task.groupReporting'),
    actions: [{ key: 'log', label: t('task.logs'), icon: IconFile }],
  },
  {
    label: t('delete.title'),
    actions: [
      { key: 'delete', label: t('task.delete'), icon: IconTrash, disabled: isActive.value },
    ],
  },
])

function onMenuSelect(key: string): void {
  if (key === 'delete') {
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
      <!-- ① 名称行：任务名 + 提示 chip + 快照数 chip（**无状态徽章**——
           徽章固定在 ② 指标行「用时」之后，即它本来的位置；owner 2026-10-24 两次裁决：
           ①「上次成功」只留左边的那个（指标文案），② 徽章不要摆到右侧按钮旁边）。 -->
      <div class="rw-row-card__name">
        <n-text strong class="task-row__name">{{ task.name }}</n-text>
        <StatusTag v-if="stale" kind="warning" :label="t('task.stale7d')" />
        <n-tooltip v-if="task.prune_due" trigger="hover">
          <template #trigger>
            <StatusTag kind="warning" :label="t('task.pruneDue')" />
          </template>
          {{ t('task.pruneDueNote') }}
        </n-tooltip>
        <n-tag v-if="snapshotLabel" size="small" :bordered="false" type="default">{{ snapshotLabel }}</n-tag>
      </div>

      <!-- ② 指标行：三项指标 + **「上次结果」徽章（紧跟「用时」之后，即原位置）**。
           原生 flex 恒不换行，保住虚拟滚动的固定行高；徽章不再往行尾推（margin-left:auto
           会把它顶到右侧按钮旁，那是 owner 否决的形态）。徽章文案走**短状态词**，
           否则与「上次成功备份于」撞成两个「上次成功」。 -->
      <div class="task-row__metrics">
        <n-text depth="3" class="task-row__metric"
          >{{ lastBackupLabel }}<n-text depth="2">{{ lastBackupValue }}</n-text></n-text
        >
        <n-text depth="3" class="task-row__metric"
          >{{ t('task.next_run') }}：<n-text depth="2">{{ nextRunText }}</n-text></n-text
        >
        <n-text depth="3" class="task-row__metric"
          >{{ t('task.duration') }}：<n-text depth="2">{{ durationText }}</n-text></n-text
        >
        <span v-if="lastResultText" class="task-row__metric task-row__result">
          <n-text depth="3">{{ t('task.last_result_label') }}</n-text>
          <StatusTag :kind="kind" :label="lastResultText" />
        </span>
      </div>
    </div>

    <!-- ③ 操作区
         用原生 flex div 而非 n-space：n-space 展平插槽子节点时**按索引发 key**，而这里
         4 个兄弟全是独立 v-if（空闲=2 个 / 运行中=3 个），节点数抖动会让索引相撞——
         实测「运行中 → 跑完回落空闲」会残留两个「立即运行」按钮，刷新才消失。
         原生 div 的子节点按位置全量 diff，不受此影响（本项目既有纪律：条件组一律用原生 flex）。

         三键同形态（owner 2026-10-24）：统一 size="small" + 有边框的 default 档 + `#icon` 插槽，
         「开始」= 播放图标 + 文案（保留 primary 主色作唯一层次差异）；编辑画笔与三点菜单触发器
         不再是无边框的 quaternary 裸图标——那样看不出是按钮，与「开始」也不同档。 -->
    <div class="rw-row-card__actions">
      <div class="task-row__actions">
        <n-tooltip v-if="startBlocked && !isActive" trigger="hover">
          <template #trigger>
            <n-button size="small" type="primary" disabled>
              <template #icon>
                <n-icon :component="IconPlay" />
              </template>
              {{ t('task.start') }}
            </n-button>
          </template>
          {{ t('wizard.rescan_required') }}
        </n-tooltip>
        <n-button v-else-if="!isActive" size="small" type="primary" @click="emit('start', task.id)">
          <template #icon>
            <n-icon :component="IconPlay" />
          </template>
          {{ t('task.start') }}
        </n-button>
        <n-button v-if="isActive && runningJobId" size="small" @click="emit('cancel', runningJobId)">
          {{ t('task.cancel') }}
        </n-button>
        <n-button v-if="runningJobId" size="small" @click="emit('log', task.id)">
          {{ t('task.logs') }}
        </n-button>
        <!-- 编辑：行内画笔（图标按钮必须有 aria-label，见 a11y-baseline） -->
        <n-button
          size="small"
          :aria-label="t('task.edit')"
          @click="emit('edit', task.id)"
        >
          <template #icon>
            <n-icon :component="IconEdit" />
          </template>
        </n-button>
        <TaskActionsMenu :groups="menuGroups" :aria-label="t('task.menu')" @select="onMenuSelect" />
      </div>
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

/* 原生 flex 恒不换行 + 溢出裁剪：行卡是虚拟滚动的固定行高（dashboard ROW_HEIGHT=84），
   指标换行会把行撑高导致切片错位；窄屏宁可裁掉尾部指标，也不破坏行高。 */
.task-row__metrics {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: nowrap;
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
}

.task-row__metric {
  white-space: nowrap;
}

/* 上次结果：标签与徽章贴在一起（徽章紧跟「用时」，不再 margin-left:auto 顶到按钮旁） */
.task-row__result {
  display: flex;
  align-items: center;
  gap: 4px;
}


/* 操作区：原生 flex 替代 n-space（原因见模板内注释）——gap 与不换行由本类承担 */
.task-row__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}
</style>
