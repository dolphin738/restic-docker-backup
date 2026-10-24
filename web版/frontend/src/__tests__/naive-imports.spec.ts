/**
 * 护栏：naive-ui 为**按需导入**（无全局注册、无 unplugin-vue-components 自动导入插件）。
 *
 * 漏导入的后果是静默降级而非报错：Vue 把 `<n-select>` 当未知原生元素渲染，
 * `vue-tsc` 与 `vite build` 都不检查未注册组件名 —— 两道 CI 门全部放行，
 * 只有真机打开页面才暴露（R-023 仓库切换器即因此顶栏空转：rect 0×0、点不动）。
 *
 * 本用例在编译期之外补上这道检查：模板里出现的 `n-*` 标签必须能在同文件的
 * `import { ... } from 'naive-ui'` 列表中找到对应的大驼峰名。
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC_DIR = join(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      walk(full, out)
    // 只扫 .vue：`n-*` 模板语法只在 SFC 里出现。.ts（含 spec）里的 `<n-xxx>` 一律是
    // 字符串字面量或注释（实测 4 处误报：a11y-baseline 断言串 / 本文件注释 /
    // icons.ts 注释 / wizardEnabledHint.spec 串），纳入扫描只会制造假阳性。
    } else if (/\.vue$/.test(entry.name)) {
      out.push(full)
    }
  }
  return out
}

function pascal(tag: string): string {
  return tag
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('')
}

function missingImports(src: string): string[] {
  // 全文扫描 `<n-*`（不再按 <template> 块切分）——原实现用非贪婪 `<template…</template>`
  // 取块，遇到嵌套 `<template #header-extra>` / `<template #icon>` 的页面会在**第一个**
  // `</template>` 处截断，其后所有 `n-*` 逃脱检查（实证：快照页 5 处 `n-input` 漏导入，
  // 护栏却全绿 → 生产环境该组件 unresolved、输入框整片不渲染）。`n-` 前缀只可能出现在
  // 模板里（script 段是 `h(NInput)` 形态，不含 `<`），故全文扫描安全且更严。
  const used = new Set<string>()
  for (const m of src.matchAll(/<n-[a-z0-9-]+/g)) {
    used.add(m[0].slice(1))
  }
  if (used.size === 0) {
    return []
  }

  // 同一文件内所有 `from 'naive-ui'` 的具名导入（含多行导入块）
  const imported = new Set<string>()
  const blocks = src.match(/import\s*\{[\s\S]*?\}\s*from\s*['"]naive-ui['"]/g) ?? []
  for (const block of blocks) {
    for (const m of block.matchAll(/\b(N[A-Z][A-Za-z0-9]*)\b/g)) {
      imported.add(m[1])
    }
  }

  return [...used].filter((tag) => !imported.has(pascal(tag))).sort()
}

describe('naive-ui 按需导入完整性', () => {
  const files = walk(SRC_DIR)

  it('src 目录应当被扫描到（防止路径/环境差异导致空跑）', () => {
    expect(files.length).toBeGreaterThan(20)
  })

  it('模板中出现的 n-* 标签均在同文件导入', () => {
    const offenders: string[] = []
    for (const file of files) {
      const missing = missingImports(readFileSync(file, 'utf8'))
      if (missing.length > 0) {
        offenders.push(`${file.replace(SRC_DIR, 'src')}: ${missing.join(', ')}`)
      }
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })
})
