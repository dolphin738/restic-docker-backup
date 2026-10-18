/**
 * 护栏：语言包文案里不得出现**未转义的 `@`**。
 *
 * 背景（2026-10-19 真机 E2E 抓到）：vue-i18n 把 `@` 当作 linked message 语法起点
 * （`@:key`）。`repo.urlPlaceholder` 等文案里的 `sftp:user@host:/path` 会让 vue-i18n
 * 在**每次渲染该文案时**抛 `Message compilation error: Invalid linked format`，
 * 并在控制台刷错误——但页面照样显示（编译失败时回退原文），所以 vue-tsc / vitest /
 * build 三道门全部放行，只有真机控制台才看得见。
 *
 * 结论：邮箱、URL、ssh 地址里的 `@` 必须写成字面量插值 `{'@'}`。
 * 本用例把「只能靠真机发现」的问题变成 CI 可拦的静态检查。
 */
import { describe, expect, it } from 'vitest'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'

type Nested = Record<string, unknown>

/** 收集叶子文案（`section.key` → 原始字符串）。 */
function leafMessages(node: unknown, prefix = ''): Array<[string, string]> {
  if (node === null || typeof node !== 'object') {
    return []
  }
  const out: Array<[string, string]> = []
  for (const [key, value] of Object.entries(node as Nested)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (value !== null && typeof value === 'object') {
      out.push(...leafMessages(value, path))
    } else if (typeof value === 'string') {
      out.push([path, value])
    }
  }
  return out
}

/** 去掉合法写法 `{'@'}` 后，若仍残留 `@` 即判定为隐患。 */
function hasRawAt(message: string): boolean {
  return message.replace(/\{'@'\}/g, '').includes('@')
}

describe('i18n 文案语法：@ 必须转义', () => {
  it('zh-CN 无未转义的 @（linked message 语法冲突）', () => {
    const offenders = leafMessages(zhCN)
      .filter(([, message]) => hasRawAt(message))
      .map(([path]) => path)
    expect(offenders, `未转义 @ 的键：${offenders.join(', ')}`).toEqual([])
  })

  it('en-US 无未转义的 @（linked message 语法冲突）', () => {
    const offenders = leafMessages(enUS)
      .filter(([, message]) => hasRawAt(message))
      .map(([path]) => path)
    expect(offenders, `未转义 @ 的键：${offenders.join(', ')}`).toEqual([])
  })
})
