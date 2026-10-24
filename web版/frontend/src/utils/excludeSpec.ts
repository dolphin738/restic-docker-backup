/** 批次② 过滤条件模型（设计方案 §2.2–§2.4）：快捷卡 + 有序规则列表。
 *
 * 本模块是**纯函数**（无 Vue 依赖），承担三件事：
 *   1. glob / 大小阈值的**前端实时校验**（与后端 `exclude_spec.py` 同口径；后端仍 422 兜底）；
 *   2. 展平为 restic 参数（quick → 锚定 pattern；rules 有序；negate/icase 分流）；
 *   3. 由扁平 `excludes` 反推 spec（编辑预填；有损，icase 无法还原）。
 *
 * 探针结论（restic 0.18.0）：`negate ∧ icase` 组合禁用——`--exclude` 负向与 `--iexclude`
 * 跨 flag 互不生效；UI 负向行隐藏 icase 开关。
 */

export interface ExcludeRule {
  pattern: string
  negate: boolean
  icase: boolean
}

export interface QuickExcludes {
  extensions: string[]
  /** 名称包含（多个；各生成一条 任意层级含名字 的宽规则）。 */
  nameContains: string[]
  largerThan: string | null
  ifPresent: string[]
  caches: boolean
}

export interface ExcludeSpec {
  quick: QuickExcludes
  rules: ExcludeRule[]
}

export interface FlattenedExcludes {
  excludes: string[]
  iexcludes: string[]
  largerThan: string | null
  ifPresent: string[]
  caches: boolean
}

/** glob 语法错误码（null = 合法）。 */
export type GlobError = 'empty' | 'brace' | 'unclosed' | 'emptyClass' | null

const SIZE_RE = /^\d+(?:\.\d+)?[kKmMgGtT]?$/

export function emptyQuick(): QuickExcludes {
  return { extensions: [], nameContains: [], largerThan: null, ifPresent: [], caches: false }
}

export function emptyExcludeSpec(): ExcludeSpec {
  return { quick: emptyQuick(), rules: [] }
}

/** 校验 restic（Go filepath.Match）glob 语法；返回错误码或 null。
 * 与后端 `exclude_spec.validate_glob` 同口径：空 / 花括号（静默空转）/ 未闭合 `[` / 空类。 */
export function validateGlob(pattern: string): GlobError {
  const p = pattern
  if (!p || !p.trim()) {
    return 'empty'
  }
  if (p.includes('{') || p.includes('}')) {
    return 'brace'
  }
  let i = 0
  const n = p.length
  while (i < n) {
    const ch = p[i]
    if (ch === '\\') {
      i += 2
      continue
    }
    if (ch === '[') {
      let j = i + 1
      if (j < n && (p[j] === '^' || p[j] === '!')) {
        j += 1
      }
      const start = j
      while (j < n && p[j] !== ']') {
        j += p[j] === '\\' ? 2 : 1
      }
      if (j >= n) {
        return 'unclosed'
      }
      if (j === start) {
        return 'emptyClass'
      }
      i = j + 1
      continue
    }
    i += 1
  }
  return null
}

/** 校验 `--exclude-larger-than` 取值（如 100M）；返回是否合法。 */
export function isValidSize(size: string): boolean {
  return SIZE_RE.test((size || '').trim())
}

/** 锚定规则 pattern：以 `/` 开头视为展示路径 → 补源根前缀（已是容器形态则原样）。
 * 纯 glob（不以 `/` 开头）原样返回。用于「生效预览」。 */
export function anchorPattern(pattern: string, sourceRoot: string): string {
  const root = (sourceRoot || '').replace(/\/+$/, '')
  if (!pattern.startsWith('/')) {
    return pattern
  }
  if (!root) {
    return pattern
  }
  if (pattern === root || pattern.startsWith(`${root}/`)) {
    return pattern
  }
  return `${root}${pattern}`
}

/** 展平 ExcludeSpec → restic 参数（与后端 `flatten_exclude_spec` 同口径，供预览与下发）。 */
export function flattenExcludes(spec: ExcludeSpec, sourceRoot: string): FlattenedExcludes {
  const excludes: string[] = []
  const iexcludes: string[] = []

  for (const raw of spec.quick.extensions) {
    let ext = raw.trim()
    if (!ext) {
      continue
    }
    if (!ext.startsWith('.')) {
      ext = `.${ext}`
    }
    excludes.push(`**/*${ext}`)
  }
  for (const raw of spec.quick.nameContains) {
    const name = raw.trim()
    if (name) {
      excludes.push(`**/*${name}*`)
    }
  }

  for (const rule of spec.rules) {
    const raw = rule.pattern.trim()
    if (!raw) {
      continue
    }
    // negate ∧ icase 禁用：调用方应已阻止，这里做防御性降级（按 negate 处理，忽略 icase）
    const anchored = anchorPattern(raw, sourceRoot)
    if (rule.negate) {
      excludes.push(`!${anchored}`)
    } else if (rule.icase) {
      iexcludes.push(anchored)
    } else {
      excludes.push(anchored)
    }
  }

  return {
    excludes,
    iexcludes,
    largerThan: spec.quick.largerThan ? spec.quick.largerThan.trim() : null,
    ifPresent: spec.quick.ifPresent.map((s) => s.trim()).filter(Boolean),
    caches: spec.quick.caches,
  }
}

/** 由扁平 excludes 反推 spec（编辑预填，有损：icase 与快捷卡来源无法还原，按规则行处理）。 */
export function specFromFlatExcludes(excludes: string[]): ExcludeSpec {
  const rules: ExcludeRule[] = []
  for (const raw of excludes) {
    const item = raw.trim()
    if (!item) {
      continue
    }
    if (item.startsWith('!')) {
      rules.push({ pattern: item.slice(1), negate: true, icase: false })
    } else {
      rules.push({ pattern: item, negate: false, icase: false })
    }
  }
  return { quick: emptyQuick(), rules }
}
