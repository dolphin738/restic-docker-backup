/**
 * 恢复向导步骤 1「已选文件清单」护栏（owner 2026-10-26 反馈）。
 *
 * 背景：第一步勾选文件后，界面只有一个「已选 N 个」数字，用户无法核对**到底选了哪些**，
 * 且当时文件树的选中态没有任何样式（类名挂上了、CSS 规则缺失，见 VirtualTree 的
 * 「状态类名与样式规则一致」护栏）。本用例锁定清单侧的行为：
 *  - 已选路径**逐条列出**（排序稳定，不随点击顺序抖动）；
 *  - 每条可单独取消（emit remove-file，路径原样透传）；
 *  - 可一键清空（emit clear-files）。
 *
 * 只挂步骤 1 组件本身（snapshotId=null → 不触发文件树请求），与编排器解耦。
 */

import { flushPromises, mount } from '@vue/test-utils'
import naive from 'naive-ui'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { i18n } from '@/i18n'
import SelectFiles from '@/pages/snapshots/restore/select-files.vue'

// 只 mock 文件树/搜索两个 API：清单用例传 snapshotId=null 不会触发，
// 树高度用例传 snapshotId 才会真的发请求并渲染树。
vi.mock('@/api/snapshots', () => ({
  listSnapshotFiles: vi.fn(async () => ({
    path: '/',
    items: [
      { path: '/etc', name: 'etc', type: 'dir', display_path: '/etc', size_bytes: 0, mtime: null },
      { path: '/var', name: 'var', type: 'dir', display_path: '/var', size_bytes: 0, mtime: null },
    ],
    next_cursor: null,
  })),
  searchSnapshotFiles: vi.fn(async () => ({ items: [], truncated: false })),
}))

function mountStep(selected: string[], snapshotId: string | null = null) {
  return mount(SelectFiles, {
    props: {
      snapshotId,
      snapshotOptions: [],
      snapshotsLoading: false,
      snapshotsError: null,
      selectedFiles: new Set(selected),
    },
    global: { plugins: [i18n, naive] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
})

describe('恢复向导步骤 1：已选文件清单', () => {
  it('已选路径逐条列出且按字典序稳定排序', () => {
    const w = mountStep(['/var/log', '/etc/a.conf'])
    const items = w.findAll('.restore__picked-item')
    expect(items).toHaveLength(2)
    const paths = items.map((item) => item.find('.restore__picked-path').text())
    expect(paths).toEqual(['/etc/a.conf', '/var/log'])
    w.unmount()
  })

  it('未选任何文件时不渲染清单，改为「恢复整个快照」提示', () => {
    const w = mountStep([])
    expect(w.find('.restore__picked').exists()).toBe(false)
    expect(w.text()).toContain('将恢复整个快照')
    w.unmount()
  })

  it('点清单项的取消按钮 → emit remove-file（路径原样透传）', async () => {
    const w = mountStep(['/etc/a.conf', '/var/log'])
    // 第二条即 /var/log（已排序）
    await w.findAll('.restore__picked-item')[1].find('button').trigger('click')
    expect(w.emitted('remove-file')).toEqual([['/var/log']])
    w.unmount()
  })

  it('点「清空已选」→ emit clear-files', async () => {
    const w = mountStep(['/etc/a.conf'])
    // 清空按钮在清单容器顶部（文本可辨识），逐条取消按钮只有图标
    const clear = w.findAll('.restore__picked button').find((btn) => btn.text().includes('清空'))
    expect(clear).toBeTruthy()
    await clear!.trigger('click')
    expect(w.emitted('clear-files')).toHaveLength(1)
    w.unmount()
  })
})

/**
 * 树视口高度护栏（owner 2026-10-26）。
 *
 * 原实现给恢复树传死高 520px（P9 满高决定）：快照根只有两三个条目时，下方留下
 * 大片死空白，把「已选清单」推出首屏。改为 VirtualTree 的 auto 模式（内容行数 ×
 * 行高，min/max 夹住）后，这里用**实测像素**钉住两件事：
 *  1. 视口高度按 token 保底（行数少 → 等于 minHeight），不再是被写死的 520；
 *  2. 高度必须 < 520（旧值），防止有人把常量加回去又静默退化。
 */
describe('恢复向导步骤 1：文件树视口高度（内容驱动）', () => {
  it('2 行内容时按 minHeight 保底，且不再固定 520px', async () => {
    const w = mountStep([], 'snap-1')
    await flushPromises()
    const viewport = w.find('.vtree__viewport')
    expect(viewport.exists()).toBe(true)
    const style = viewport.attributes('style') ?? ''
    const matched = /height:\s*(\d+)px/.exec(style)
    expect(matched).not.toBeNull()
    const height = Number(matched![1])
    expect(height).toBeGreaterThanOrEqual(200) // 空快照也有可用树盒
    expect(height).toBeLessThan(520) // 铁证：旧固定 520 已被移除
    w.unmount()
  })
})
