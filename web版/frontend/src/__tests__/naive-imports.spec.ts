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
    } else if (/\.(vue|ts)$/.test(entry.name)) {
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
  const templates = src.match(/<template[\s\S]*?<\/template>/g) ?? []
  const used = new Set<string>()
  for (const block of templates) {
    for (const m of block.matchAll(/<n-[a-z0-9-]+/g)) {
      used.add(m[0].slice(1))
    }
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
