/** 审计展示层共用格式化（E26）。
 *
 * 单一事实来源：审计页与归档页「操作日志」抽屉都要把 audit_logs 行渲染成人能读的
 * 样子，两处各写一份必然漂移（键名译文缺一处就会一处中文一处英文）。故收口到这里。
 *
 * 两条纪律（与审计页原实现一致，勿"优化"掉）：
 *   1. **缺键一律回落原文**——宁可显示英文动作名/键名，也不显示空标签或 `[object]`；
 *   2. **detail 可能不是 JSON**（契约不保证）→ 解析失败原样透传，不抛、不吞。
 *
 * `t` / `te` 由调用方注入（不直接 import i18n 单例）：便于单测，也避免 utils 层
 * 反向依赖 i18n 实例。
 */

/**
 * 把 detail（JSON 文本原文）解析成对象。
 *
 * @returns 解析成功且是「非数组对象」时返回它；否则 null（调用方回落原文展示）。
 */
export function parseAuditDetail(detail: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(detail) as unknown
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

/** 动作名 → 本地化文案（键 `audit.action.<action>`）；未收录回落原文。 */
export function auditActionLabel(action: string, t: (k: string) => string, te: (k: string) => boolean): string {
  const key = `audit.action.${action}`
  return te(key) ? t(key) : action
}

/** detail 键名 → 本地化文案（键 `audit.detailKey.<key>`）；未收录回落原键名。 */
export function auditDetailKeyLabel(
  key: string,
  t: (k: string) => string,
  te: (k: string) => boolean,
): string {
  const i18nKey = `audit.detailKey.${key}`
  return te(i18nKey) ? t(i18nKey) : key
}

/** detail 值 → 可读文本：布尔走 是/否、耗时补单位、数组顿号连接、对象回落 JSON。 */
export function auditDetailValue(key: string, value: unknown, t: (k: string) => string): string {
  if (value === null || value === undefined) {
    return '—'
  }
  if (typeof value === 'boolean') {
    return value ? t('audit.value.yes') : t('audit.value.no')
  }
  if (key === 'latency_ms' && typeof value === 'number') {
    return `${value} ms`
  }
  if (Array.isArray(value)) {
    return value.length ? value.map((item) => String(item)).join('、') : '—'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}
