/**
 * 护栏：中英文语言包的**键集合**必须完全一致。
 *
 * 背景：R-023 多仓库前端当时只补了 zh-CN，en-US 缺 25 个键——英文界面下这些文案
 * 会静默回退显示键名（如 `repo.newRepo`）。这类漂移既不会被 vue-tsc 发现，也不会
 * 被 vite build 拦下，只有切到英文界面逐页看才会暴露。
 *
 * 本用例直接 import 两个语言包对象（而非正则解析文本），因此嵌套段（如
 * `snapshot.forget.title`）同样受检，且不受源码格式/注释影响。
 */
import { describe, expect, it } from 'vitest'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'

type Nested = Record<string, unknown>

/** 收集叶子键路径（`section.key` / `section.group.key`）；中间对象本身不算叶子。 */
function leafPaths(node: unknown, prefix = ''): string[] {
  if (node === null || typeof node !== 'object') {
    return []
  }
  const out: string[] = []
  for (const [key, value] of Object.entries(node as Nested)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (value !== null && typeof value === 'object') {
      out.push(...leafPaths(value, path))
    } else {
      out.push(path)
    }
  }
  return out
}

const zhKeys = new Set(leafPaths(zhCN))
const enKeys = new Set(leafPaths(enUS))

describe('i18n 语言包键集合一致性', () => {
  it('两个语言包都解析出足够多的键（防止解析失败导致空跑）', () => {
    expect(zhKeys.size).toBeGreaterThan(150)
    expect(enKeys.size).toBeGreaterThan(150)
  })

  it('en-US 不缺 zh-CN 的任何键', () => {
    const missing = [...zhKeys].filter((key) => !enKeys.has(key)).sort()
    expect(missing, `en-US 缺失 ${missing.length} 项：\n${missing.join('\n')}`).toEqual([])
  })

  it('zh-CN 不缺 en-US 的任何键', () => {
    const missing = [...enKeys].filter((key) => !zhKeys.has(key)).sort()
    expect(missing, `zh-CN 缺失 ${missing.length} 项：\n${missing.join('\n')}`).toEqual([])
  })
})
