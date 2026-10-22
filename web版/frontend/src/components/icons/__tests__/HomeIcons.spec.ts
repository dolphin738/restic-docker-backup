/**
 * 护栏：HomeIcons 图标集（UI全量改造方案 §4.7 P4 —— L3 图标层）。
 *
 * P4 验收口径 = typecheck + naive-imports 护栏 + 目视；本 spec 补上「目视」之外的
 * 机械护栏，盯三件容易静默漂移的事：
 * ① 统一风格：20×20 网格 / 描边 1.5 / fill none / currentColor（亮暗同源）；
 * ② 禁止散落：新图标只进 HomeIcons.ts 单一文件，源码只允许 import vue；
 * ③ 防误删：主页（P7）与恢复向导（P8–P11）的关键图标必须仍在导出表里。
 *
 * 目视产物由一次性预览页承担（不入库，验证后删除）。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import * as icons from '../HomeIcons'

const SRC_FILE = join(process.cwd(), 'src/components/icons/HomeIcons.ts')

/** P4 交付时登记在册的图标数：新增图标必须同步改这里（ deliberate，防静默增删）。
 *  2026-10-22：IconGauge 随限速任务级裁决移除（状态条不设运行中限速入口）→ 34→33。 */
const REGISTERED_COUNT = 33

describe('HomeIcons 图标集（P4）', () => {
  it('导出规模与登记表一致', () => {
    expect(Object.keys(icons).length).toBe(REGISTERED_COUNT)
  })

  it('导出命名全部 Icon 前缀 + PascalCase（无默认导出混入）', () => {
    for (const name of Object.keys(icons)) {
      expect(name, name).toMatch(/^Icon[A-Z][A-Za-z0-9]*$/)
    }
  })

  it('主页与恢复向导关键图标在位（防误删）', () => {
    // 主页（P7）：行动卡 / 状态条 / 工具栏 / 任务卡与菜单
    const home = [
      'IconPlusCircle',
      'IconPlay',
      'IconStop',
      'IconAbort',
      'IconActivity',
      'IconList',
      'IconGrid',
      'IconClock',
      'IconFilter',
      'IconSortAsc',
      'IconSortDesc',
      'IconLayers',
      'IconCalendar',
      'IconCheckCircle',
      'IconEdit',
      'IconTrash',
      'IconDownload',
      'IconShieldCheck',
      'IconAlertTriangle',
    ]
    // 恢复入口页与向导（P8–P11）
    const restore = [
      'IconServer',
      'IconFolder',
      'IconFolderOpen',
      'IconFile',
      'IconUpload',
      'IconSearch',
      'IconChevronRight',
      'IconChevronDown',
      'IconArrowLeft',
      'IconX',
      'IconLock',
      'IconCopy',
      'IconSliders',
      'IconInfo',
    ]
    for (const name of [...home, ...restore]) {
      expect(icons, name).toHaveProperty(name)
    }
  })

  it('每个图标都是统一风格：20 网格 + 描边 1.5 + fill none + currentColor', () => {
    for (const [name, component] of Object.entries(icons)) {
      const wrapper = mount(component)
      const svg = wrapper.element
      expect(svg.tagName.toLowerCase(), name).toBe('svg')
      expect(svg.getAttribute('viewBox'), name).toBe('0 0 20 20')
      expect(svg.getAttribute('stroke-width'), name).toBe('1.5')
      expect(svg.getAttribute('stroke'), name).toBe('currentColor')
      expect(svg.getAttribute('fill'), name).toBe('none')
      expect(svg.getAttribute('width'), name).toBe('20')
      expect(svg.getAttribute('height'), name).toBe('20')
      expect(svg.childNodes.length, `${name} 不应是空壳`).toBeGreaterThan(0)
    }
  })

  it('零依赖：源码只允许 import vue（禁止散落 / 禁引 naive-ui 与其它图标文件）', () => {
    const src = readFileSync(SRC_FILE, 'utf8')
    const importFroms = [...src.matchAll(/from\s*['"]([^'"]+)['"]/g)].map((m) => m[1])
    expect(importFroms).toEqual(['vue'])
  })
})
