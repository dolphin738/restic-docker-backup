/** 任务展示格式化（单一实现点）：状态语义色 / 耗时 / 时间口径文案。
 *
 * 存在理由（规范 §3.2 单一实现点）：改造中同一套规则曾出现三份拷贝——
 * `TaskCard.vue` 的 `showTime` / `statusTagType` / `durationText` 与
 * `pages/dashboard/index.vue` 的 `tableTime` / `statusKind` / `formatDuration`
 * 逐字重复，改口径必漏一处。现统一收口到此，list 行卡与 details 表格共用。
 *
 * 纪律：本文件只做**纯函数**（无组件、无 IO、无 i18n 实例依赖——`t` 由调用方注入），
 * 便于单测；文案键仍在 `i18n/locales/*`，不在此硬编码中文。
 */

import { formatDateTime, formatDateTimeOr } from '@/utils/datetime'
import { relativeTime } from '@/utils/relativeTime'

/** 状态徽章语义色：running/pending=info，success=success，failed=error，interrupted=warning。 */
export type StatusKind = 'default' | 'info' | 'success' | 'warning' | 'error'

/**
 * 任务状态 → 徽章语义色。
 *
 * @param status 后端派生状态（paused/running/pending/success/failed/interrupted/cancelled/never_run）。
 * @returns Naive UI 的 tag type。
 */
export function statusKind(status: string): StatusKind {
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

/**
 * 毫秒耗时 → 紧凑展示（`H:MM:SS` 或 `M:SS`）。
 *
 * @param ms 毫秒；负数按「无效」处理（后端已过滤时钟回拨，此处再兜一层）。
 * @returns 展示串；负数返回空串（调用方决定是否渲染占位）。
 */
export function formatDurationMs(ms: number): string {
  if (ms < 0) {
    return ''
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

/** 翻译函数最小签名（与 vue-i18n `t` 兼容；避免在本文件依赖 i18n 实例）。 */
export type TranslateFn = (key: string, named?: Record<string, unknown>) => string

/** 任务 tag 形态 `task-<id>`：服务端 `task_repo` 单一生成点，备份时打进快照 tags。 */
const TASK_TAG_RE = /^task-\d+$/

/**
 * 快照 tags → 任务名。
 *
 * 快照本身不带任务字段（restic 快照只有 id/time/host/tags/paths），任务名只能由
 * `task-<id>` 标签反查任务列表得到。口径：
 *   - 无任务标签（手工提交的 backup）→ fallback；
 *   - 任务已被删除 → 退回原始 tag（不显示空白，用户还能按 tag 追查）。
 *
 * @param tags 快照 tags。
 * @param tasks 任务列表（只需 name/tag 两个字段）。
 * @param fallback 无任务标签时的占位（如「—」）。
 */
export function taskNameFromTags(
  tags: string[],
  tasks: Array<{ name: string; tag: string }>,
  fallback: string,
): string {
  const taskTag = tags.find((tag) => TASK_TAG_RE.test(tag))
  if (!taskTag) {
    return fallback
  }
  return tasks.find((task) => task.tag === taskTag)?.name ?? taskTag
}

/**
 * 时间展示收口（P7 D4）：absolute=绝对时间；relative=相对时间，超 30 天回落绝对、无效值回落 fallback。
 *
 * @param value ISO 串 / null。
 * @param mode 'actual' | 'relative'。
 * @param t 翻译函数（注入，便于纯函数单测）。
 * @param fallback 无值时的兜底文案（如「无」「仅手动」）。
 * @returns 可直接渲染的文案。
 */
export function timeText(
  value: string | null,
  mode: 'actual' | 'relative',
  t: TranslateFn,
  fallback: string,
): string {
  if (!value) {
    return fallback
  }
  if (mode !== 'relative') {
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
  const unit =
    r.unit === 'min' ? t('dashboard.rel.min') : r.unit === 'hour' ? t('dashboard.rel.hour') : t('dashboard.rel.day')
  const dir = r.future ? t('dashboard.rel.later') : t('dashboard.rel.ago')
  return `${r.amount}${unit}${dir}`
}
