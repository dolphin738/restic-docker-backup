/** 挂载点快跳跳转行为护栏（owner 2026-10-24 报告：点击后无法真正跳转）。
 *
 * 用真实 VirtualTree + 按路径分流的 browseSources mock 复现：
 *   ① 挂载点 chips 应从**模式根**响应填充；
 *   ② 点击 chip → currentPath 变化 → 树应加载该目录的子项（真跳转）；
 *   ③ 非根响应**不得清空** chips（导航条要常驻——旧实现每次响应都覆盖 mountChips，
 *      非根请求回 mounts=[] → chips 瞬间消失，视觉上等于「点了没反应」）。
 */

import { mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { h, nextTick, reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SourcesPicker from '@/components/task/SourcesPicker.vue'
import VirtualTree from '@/components/VirtualTree.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'
import { browseSources } from '@/api/backupTasks'
import type { ExcludeSpec } from '@/utils/excludeSpec'

vi.mock('@/api/backupTasks', () => ({
  browseSources: vi.fn(),
}))

vi.mock('@/api/exclusions', () => ({
  getExclusions: vi.fn(async () => ({ display_items: [] })),
}))

const browseMock = vi.mocked(browseSources)
browseMock.mockImplementation(async (path?: string) => {
  const p = path ?? '/'
  if (p === '/' || p === '') {
    return {
      path: '/',
      mounts: ['/data', '/photos'],
      items: [
        { name: 'data', type: 'dir', display_path: '/data', is_mountpoint: true },
        { name: 'photos', type: 'dir', display_path: '/photos', is_mountpoint: true },
      ],
      next_cursor: null,
    }
  }
  return {
    path: p,
    mounts: [],
    items: [{ name: 'sub', type: 'dir', display_path: `${p}/sub`, is_mountpoint: false }],
    next_cursor: null,
  }
})

/** 等树行真正渲染出来（不能用 textContent 判断——挂载点 chip 的文字先于树行出现，
 *  会让等待循环提前退出，点名称时 find 静默失败，表现为「点击没生效」）。 */
async function waitTreeRows(wrapper: { element: Element }): Promise<void> {
  for (let i = 0; i < 40; i += 1) {
    await nextTick()
    const names = [...wrapper.element.querySelectorAll('.vtree__name')].map(
      (el) => el.textContent?.trim() ?? '',
    )
    if (names.includes('data')) {
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
}

async function mountPicker() {
  const wrapper = mount(
    h(
      NConfigProvider,
      { themeOverrides: lightThemeOverrides },
      {
        default: () =>
          h(NMessageProvider, null, {
            default: () => h(SourcesPicker, { sources: [], excludes: [] }),
          }),
      },
    ),
    {
      global: {
        plugins: [createPinia(), i18n, [VueQueryPlugin, { queryClient: new QueryClient() }]],
      },
    },
  )
  // 等初始加载 + 渲染（树行出现才算就绪）
  await waitTreeRows(wrapper)
  return wrapper
}

beforeEach(() => {
  browseMock.mockClear()
})

/** 有状态 wrapper：回写 update:sources / update:excludeSpec，
 *  让「点名称切换备份成员资格」的完整回路可测（受控组件单向断裂会测不到变化）。 */
async function mountStateful(sources: string[], spec?: ExcludeSpec) {
  const state = reactive<{ sources: string[]; spec?: ExcludeSpec }>({ sources, spec })
  const wrapper = mount(
    h(
      NConfigProvider,
      { themeOverrides: lightThemeOverrides },
      {
        default: () =>
          h(NMessageProvider, null, {
            default: () =>
              h(SourcesPicker, {
                sources: state.sources,
                excludes: [],
                excludeSpec: state.spec,
                'onUpdate:sources': (value: string[]) => {
                  state.sources = value
                },
                'onUpdate:excludeSpec': (value: ExcludeSpec) => {
                  state.spec = value
                },
              }),
          }),
      },
    ),
    {
      global: {
        plugins: [createPinia(), i18n, [VueQueryPlugin, { queryClient: new QueryClient() }]],
      },
    },
  )
  await waitTreeRows(wrapper)
  return { wrapper, state }
}

function clickName(wrapper: { element: Element }, name: string): void {
  const row = [...wrapper.element.querySelectorAll('.vtree__row')].find(
    (r) => r.querySelector('.vtree__name')?.textContent?.trim() === name,
  )
  row?.querySelector('.vtree__name')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

async function expandDir(wrapper: { element: Element }, name: string): Promise<void> {
  const row = [...wrapper.element.querySelectorAll('.vtree__row')].find(
    (r) => r.querySelector('.vtree__name')?.textContent?.trim() === name,
  )
  ;(row?.querySelector('.vtree__caret') as HTMLElement | undefined)?.dispatchEvent(
    new MouseEvent('click', { bubbles: true }),
  )
  for (let i = 0; i < 40; i += 1) {
    await nextTick()
    const names = [...wrapper.element.querySelectorAll('.vtree__name')].map(
      (el) => el.textContent?.trim() ?? '',
    )
    if (names.includes('sub')) {
      break
    }
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
}

describe('点名称统一切换备份成员资格（owner 2026-10-24）', () => {
  it('未覆盖 → 点名称 = 加入（sources +1 且行高亮）', async () => {
    const { wrapper, state } = await mountStateful([])
    clickName(wrapper, 'data')
    await nextTick()
    expect(state.sources).toEqual(['/data'])
    const row = [...wrapper.element.querySelectorAll('.vtree__row')].find(
      (r) => r.querySelector('.vtree__name')?.textContent?.trim() === 'data',
    )
    expect(row?.classList.contains('vtree__row--highlight')).toBe(true)
    wrapper.unmount()
  })

  it('已加入 → 再点名称 = 取消（移出 sources，高亮消失）', async () => {
    const { wrapper, state } = await mountStateful(['/data'])
    clickName(wrapper, 'data')
    await nextTick()
    expect(state.sources).toEqual([])
    const row = [...wrapper.element.querySelectorAll('.vtree__row')].find(
      (r) => r.querySelector('.vtree__name')?.textContent?.trim() === 'data',
    )
    expect(row?.classList.contains('vtree__row--highlight')).toBe(false)
    wrapper.unmount()
  })

  it('被源覆盖 → 点名称 = 排除（规则列表 +1 且行置灰）；再点 = 恢复', async () => {
    const { wrapper, state } = await mountStateful(['/data'])
    await expandDir(wrapper, 'data')
    clickName(wrapper, 'sub')
    await nextTick()
    const rules = (state.spec?.rules ?? []) ?? []
    expect(rules).toHaveLength(1)
    expect(rules[0]?.pattern).toBe('/data/sub')
    const subRow = [...wrapper.element.querySelectorAll('.vtree__row')].find(
      (r) => r.querySelector('.vtree__name')?.textContent?.trim() === 'sub',
    )
    expect(subRow?.classList.contains('vtree__row--excluded')).toBe(true)

    clickName(wrapper, 'sub')
    await nextTick()
    const rulesAfter = (state.spec?.rules ?? []) ?? []
    expect(rulesAfter).toHaveLength(0)
    wrapper.unmount()
  })

  it('取消父源选中 → 路径与名下排除规则一并清除（不同步成孤儿）', async () => {
    const { wrapper, state } = await mountStateful(['/data'], {
      quick: { extensions: [], nameContains: [], largerThan: null, ifPresent: [], caches: false },
      rules: [{ pattern: '/data/sub', negate: false, icase: false }],
    })
    clickName(wrapper, 'data')
    await nextTick()
    expect(state.sources).toEqual([])
    expect(state.spec?.rules ?? []).toHaveLength(0)
    wrapper.unmount()
  })

  it('祖先已被排除的后代 → 点名称 no-op（restic gitignore 限制，不做假开关）', async () => {
    const { wrapper, state } = await mountStateful(['/data'], {
      quick: { extensions: [], nameContains: [], largerThan: null, ifPresent: [], caches: false },
      rules: [{ pattern: '/data', negate: false, icase: false }],
    })
    await expandDir(wrapper, 'data')
    clickName(wrapper, 'sub')
    await nextTick()
    const rules = (state.spec?.rules ?? []) ?? []
    expect(rules).toHaveLength(1) // 仍是原来那条，没有新增
    wrapper.unmount()
  })
})

describe('挂载点快跳（左栏目录树）', () => {
  it('根响应填充 chips；点击 chip → 树真跳转到该目录', async () => {
    const w = await mountPicker()

    // ① chips 从根响应填充
    const chip = [...w.element.querySelectorAll('button')].find((b) => b.textContent?.trim() === '/data')
    expect(chip).toBeDefined()

    // ② 点击 chip → VirtualTree rootPath 变为 /data，且树渲染出该目录子项
    chip!.click()
    await nextTick()
    for (let i = 0; i < 20; i += 1) {
      await nextTick()
      if (w.element.textContent?.includes('sub')) {
        break
      }
      await new Promise((resolve) => setTimeout(resolve, 10))
    }
    const tree = w.findComponent(VirtualTree)
    expect(tree.props('rootPath')).toBe('/data')
    expect(browseMock).toHaveBeenCalledWith('/data', undefined)
    expect(w.element.textContent ?? '').toContain('sub')

    // ③ chips 常驻：非根响应不得清空导航条
    const chipAfter = [...w.element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === '/data',
    )
    expect(chipAfter).toBeDefined()
    w.unmount()
  })

  it('点击根 chip「/」回根目录且 chips 恢复', async () => {
    const w = await mountPicker()
    const dataChip = [...w.element.querySelectorAll('button')].find((b) => b.textContent?.trim() === '/data')
    dataChip!.click()
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 20))
    await nextTick()

    const rootChip = [...w.element.querySelectorAll('button')].find((b) => b.textContent?.trim() === '/')
    expect(rootChip).toBeDefined()
    rootChip!.click()
    for (let i = 0; i < 20; i += 1) {
      await nextTick()
      if (w.findComponent(VirtualTree).props('rootPath') === '/') {
        break
      }
      await new Promise((resolve) => setTimeout(resolve, 10))
    }
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/')
    w.unmount()
  })
})
