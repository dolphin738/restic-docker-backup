/**
 * 护栏：全站图标库 icons.ts（UI全量改造方案 §4.7 P4 L3 图标层 + P15 合并重绘）。
 *
 * P15（owner 2026-10-23 裁决 ①重绘 ②合并 ③全上 ⑤留库）后，本 spec 由
 * HomeIcons.spec.ts 改写，盯四件容易静默漂移的事：
 * ① 统一风格：**全部 57 个图标** 20×20 网格 / 描边 1.5 / fill none / currentColor
 *    （P15 把原 MenuIcons 16 个与 BackendIcons 8 个从 24 网格旧画法重绘进来，
 *     网格护栏随之扩展到全量——旧画法若回流会在此现形）；
 * ② 禁止散落：图标只进 icons.ts 单一文件，源码只允许 import vue；
 * ③ 防误删：主页 / 恢复向导 / 导航 / 存储后端四组关键图标必须仍在导出表里；
 * ④ 留库不删：登记数 = 57 图标 + BACKEND_ICONS + backendIcon，含暂无消费方的
 *    留库资产（IconPlay / IconSearch / IconFilter…），新增或移除必须同步改这里。
 *
 * 目视产物：frontend/icon-preview-p15.html（P15 生成的同式预览页，随批提交）。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { mount } from '@vue/test-utils'
import type { Component } from 'vue'
import { describe, expect, it } from 'vitest'
import * as icons from '../icons'
import { BACKEND_ICONS, backendIcon } from '../icons'

const SRC_FILE = join(process.cwd(), 'src/components/icons/icons.ts')

/** 登记在册的导出总数：59 图标 + BACKEND_ICONS 映射 + backendIcon 函数 = 61。
 *  变更必须 deliberate（防静默增删）。P15 = 33(Home) + 16(Menu 重绘) + 8(Backend 重绘)，
 *  Backend 的 IconPlusCircle 与 Home 重复定义已去重（保留 Home 版）；
 *  +1 = IconMore（三点菜单触发器，实心圆点，2026-10-24 任务行三键统一时新增）；
 *  +1 = IconArrowRight（P16 恢复向导第二步页脚「提交」，2026-10-25）；
 *  +1 = IconArchive（删除保护方案 D13 归档页导航项，2026-10-28）。 */
const REGISTERED_COUNT = 62

describe('全站图标库 icons.ts（P4 + P15 合并重绘）', () => {
  it('导出规模与登记表一致（59 图标 + 映射 + 解析函数）', () => {
    expect(Object.keys(icons).length).toBe(REGISTERED_COUNT)
    expect(Object.keys(icons).filter((n) => n.startsWith('Icon')).length).toBe(60)
  })

  it('导出命名：图标全部 Icon 前缀 + PascalCase；映射与函数名固定', () => {
    for (const name of Object.keys(icons)) {
      if (name === 'BACKEND_ICONS' || name === 'backendIcon') {
        continue
      }
      expect(name, name).toMatch(/^Icon[A-Z][A-Za-z0-9]*$/)
    }
  })

  it('主页 / 恢复向导关键图标在位（防误删）', () => {
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
      'IconArrowRight',
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

  it('P15 重绘集在册：导航 16 + 后端类型 8（24 网格旧画法已被 20 网格取代）', () => {
    const nav = [
      'IconDashboard',
      'IconRepo',
      'IconJobs',
      'IconSnapshots',
      'IconRestore',
      'IconBell',
      'IconSettings',
      'IconAudit',
      'IconMenu',
      'IconReports',
      'IconSun',
      'IconMoon',
      'IconMonitor',
      'IconUser',
      'IconCheck',
      'IconLogout',
    ]
    const backend = [
      'IconLocal',
      'IconS3',
      'IconB2',
      'IconSftp',
      'IconAzure',
      'IconGcs',
      'IconRest',
      'IconRclone',
    ]
    for (const name of [...nav, ...backend]) {
      expect(icons, name).toHaveProperty(name)
    }
    expect(Object.keys(BACKEND_ICONS).sort()).toEqual(
      ['azure', 'b2', 'gcs', 'local', 'rest', 'rclone', 's3', 'sftp'].sort(),
    )
  })

  it('每个图标都是统一风格：20 网格 + 描边 1.5 + fill none + currentColor', () => {
    // 合并后模块含 BACKEND_ICONS 映射与 backendIcon 函数，仅图标可挂载
    const iconEntries = Object.entries(icons).filter(([name]) =>
      name.startsWith('Icon'),
    ) as Array<[string, Component]>
    expect(iconEntries.length).toBe(60)
    for (const [name, component] of iconEntries) {
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

  it('backendIcon：已知类型取映射，未知类型回落本地路径', () => {
    expect(backendIcon('s3')).toBe(BACKEND_ICONS.s3)
    expect(backendIcon('no-such-type')).toBe(BACKEND_ICONS.local)
  })

  it('零依赖：源码只允许 import vue（禁止散落 / 禁引 naive-ui 与其它图标文件）', () => {
    const src = readFileSync(SRC_FILE, 'utf8')
    const importFroms = [...src.matchAll(/from\s*['"]([^'"]+)['"]/g)].map((m) => m[1])
    expect(new Set(importFroms)).toEqual(new Set(['vue']))
  })
})
