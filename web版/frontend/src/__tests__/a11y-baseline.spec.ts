/**
 * 护栏：无障碍基线（静态扫描，与 naive-imports 同一套文件遍历机制）。
 *
 * 背景（2026-10-19 全站 aria 盘点）：报告给 Duplicati 的可访问性 4.5 分源于
 * 「应用层 aria 语义为零」；本项目盘点后补齐了两类缺口并固化为本护栏——
 * ① **图标按钮**（渲染后无文本的 n-button）必须有 aria-label（读屏唯一线索）；
 * ② **n-switch** 是 role="switch" 但 naive 不会把相邻文案关联成可访问名，
 *    必须显式 aria-label（与相邻 n-text 同键，单一事实源）。
 *
 * 已知接受项（不在护栏内）：纯装饰 n-icon（SVG 无可访问名时读屏自动忽略，
 * 逐个加 aria-hidden 成本高收益低）；naive 弹层内部语义由组件库负责。
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
    } else if (entry.name.endsWith('.vue')) {
      out.push(full)
    }
  }
  return out
}

interface Finding {
  file: string
  /** 缺失 aria-label 的元素描述（含序号便于定位）。 */
  detail: string
}

function templateOf(src: string): string {
  const m = /<template>([\s\S]*)<\/template>\s*(<style|<script|$)/.exec(src)
  return m?.[1] ?? ''
}

function scanFile(file: string, rel: string): Finding[] {
  const src = readFileSync(file, 'utf-8')
  const tpl = templateOf(src)
  const findings: Finding[] = []
  let n = 0

  // ① 图标按钮：渲染后无文本（内容只有组件/svg/img 标签）
  for (const m of tpl.matchAll(/<n-button\b([^>]*)>([\s\S]*?)<\/n-button>/g)) {
    const tag = m[1]
    const text = m[2].replace(/<[^>]+>/g, '').trim()
    if (text) {
      continue
    }
    if (!/<[A-Z]\w*|<svg|<img|<n-icon/.test(m[2])) {
      continue
    }
    n += 1
    if (!/aria-label/.test(tag) && !/aria-label/.test(m[2])) {
      findings.push({ file: rel, detail: `图标按钮 #${n} 缺 aria-label` })
    }
  }

  // ② n-switch 必须有可访问名（naive 不关联相邻文案）
  for (const m of tpl.matchAll(/<n-switch\b([^>]*?)(\/>|>[\s\S]*?<\/n-switch>)/g)) {
    n += 1
    if (!/aria-label/.test(m[1])) {
      findings.push({ file: rel, detail: `n-switch #${n} 缺 aria-label` })
    }
  }

  return findings
}

describe('a11y 基线（图标按钮 / n-switch 可访问名）', () => {
  it('全部 .vue 无缺失的 aria-label', () => {
    const findings = walk(SRC_DIR).flatMap((file) =>
      scanFile(file, file.slice(SRC_DIR.length + 1).replace(/\\/g, '/')),
    )
    expect(
      findings,
      `缺失 ${findings.length} 处：\n${findings.map((f) => `${f.file}: ${f.detail}`).join('\n')}`,
    ).toEqual([])
  })

  it('扫描确实覆盖到了已知用例（防止正则失效导致空跑）', () => {
    // 仓库里至少存在一个 n-switch（ Wizard/设置/通知/快照共 10 处），正则必须能命中
    const switchCount = walk(SRC_DIR).reduce((sum, file) => {
      const tpl = templateOf(readFileSync(file, 'utf-8'))
      return sum + [...tpl.matchAll(/<n-switch\b/g)].length
    }, 0)
    expect(switchCount).toBeGreaterThanOrEqual(10)
  })
})
