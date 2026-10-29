/**
 * 归档页行为回归（2026-10-29 三条产品反馈的护栏）。
 *
 * 锁定语义：
 * - **保留策略布局与审计页一致**：标签置顶 + 数字框无步进按钮（改动前是横向
 *   标签/输入框两栏 + 带步进按钮，与审计页第二分区视觉不一）；
 * - **释放 ID / 销毁数据**：确认后弹窗**不立刻关闭**，依次呈现「执行中 → 结果」
 *   （改动前 ConfirmGate 点确认即关门 → 用户看到的正是「点了没反应」）；
 * - **rclone 恢复写回**：非破坏性动作**不进危险关键词门**，但反馈与上面两个动作
 *   **完全同款**（同一个 ConfirmGate + awaitResult：预览 → 确认 → 转圈 → 结果）；
 * - 失败时三个动作共用 `archive.opFailed` 文案（错误呈现一致）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { NConfigProvider, NInputNumber, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ArchivePage from '@/pages/archive/index.vue'
import ConfirmGate from '@/components/ConfirmGate.vue'
import SegmentedTabs from '@/components/dashboard/SegmentedTabs.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  releaseImpl: (async () => undefined) as () => Promise<void>,
  destroyImpl: (async () => undefined) as () => Promise<void>,
  rcloneRestoreImpl: (async () => undefined) as () => Promise<void>,
  logQueries: [] as Array<string[]>,
}))

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(async () => ({
    items: [
      { key: 'retention.repo_archive_days', value: '30', has_value: true, source: 'ui', sensitive: false },
      { key: 'retention.repo_tombstone_days', value: '365', has_value: true, source: 'ui', sensitive: false },
      { key: 'retention.rclone_archive_days', value: '365', has_value: true, source: 'ui', sensitive: false },
    ],
    passfile_status: 'ok',
  })),
  putConfig: vi.fn(async () => undefined),
}))

vi.mock('@/api/audit', () => ({
  queryAudit: vi.fn(async (params: { actions?: string[] }) => {
    mocks.logQueries.push(params.actions ?? [])
    return { items: [], total: 0, limit: 50, offset: 0, actions: [] }
  }),
  listAuditArchives: vi.fn(async () => []),
  exportAuditNdjson: vi.fn(async () => ''),
}))

vi.mock('@/api/repo', () => ({
  getArchiveList: vi.fn(async () => ({
    repos: [
      {
        repo_id: 'r-1',
        name: '我的仓库',
        type: 'local',
        url: '/repo/r-1',
        archived_at: '2026-10-20T08:00:00Z',
        passfile_status: 'ok',
      },
    ],
    tombstones: [],
  })),
  listRcloneArchives: vi.fn(async () => ({
    items: [
      {
        remote: 'wdav',
        type: 'webdav',
        deleted_at: '2026-10-21T08:00:00Z',
        used_by_repos: 0,
        keys: ['url', 'user'],
      },
    ],
  })),
  restoreArchivedRepo: vi.fn(async () => ({ ok: true })),
  releaseArchivedRepo: vi.fn(async () => {
    await mocks.releaseImpl()
    return { ok: true }
  }),
  deleteRepo: vi.fn(async () => {
    await mocks.destroyImpl()
    return { ok: true }
  }),
  deleteRepoTombstone: vi.fn(async () => ({ ok: true })),
  restoreRcloneArchive: vi.fn(async () => {
    await mocks.rcloneRestoreImpl()
    return { remote: 'wdav', type: 'webdav', conf_path: '/conf', action: 'created', remotes_after: [] }
  }),
  deleteRcloneArchive: vi.fn(async () => ({ ok: true })),
}))

function mountPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: ArchivePage }],
  })
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(NMessageProvider, null, { default: () => h(ArchivePage) }) },
    global: { plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router] },
  })
}

type Host = ReturnType<typeof mountPage>

/** 挂载并等到查询落地（vue-query 的解析要跨两轮微任务——只 flush 一次时表格行
 *  还没渲染，行内按钮自然也找不到）。 */
async function mountAndSettle(): Promise<Host> {
  const w = mountPage()
  await flushPromises()
  await flushPromises()
  return w
}

/** 让挂起的 promise 链走完（异步动作 + 查询刷新同样跨两轮）。 */
async function settle(): Promise<void> {
  await flushPromises()
  await flushPromises()
}

/** 弹窗经 naive teleport 到 body：VTU 无 attachTo 时文档里只剩传送过去的内容。 */
function modalText(): string {
  return document.body.textContent ?? ''
}

async function switchTab(w: Host, id: string): Promise<void> {
  await w.findComponent(SegmentedTabs).vm.$emit('update:model-value', id)
  await flushPromises()
}

/** 点表格行内的动作按钮（把确认门打开）——必须走这一步，直接 emit confirm 的话
 *  弹窗没开，也就无从验证「确认之后弹窗里显示了什么」。 */
async function clickRowAction(w: Host, label: string): Promise<void> {
  const all = [...w.element.querySelectorAll('button')]
  const btn = all.find((el) => (el.textContent ?? '').trim() === label) as
    | HTMLButtonElement
    | undefined
  expect(
    btn,
    `未找到行内按钮「${label}」；现有按钮：${all.map((b) => b.textContent?.trim()).join('|')}`,
  ).toBeTruthy()
  btn!.click()
  await flushPromises()
}

/** 走到确认门第 3 步并点确认。本组用例验证的是「确认之后发生了什么」，
 *  确认门自身的三步步进由 ConfirmGate 自己的用例覆盖，此处直接驱动 confirm。 */
async function confirmGate(w: Host, gateIndex: number): Promise<void> {
  const gates = w.findAllComponents(ConfirmGate)
  expect(gates.length).toBeGreaterThan(gateIndex)
  gates[gateIndex]!.vm.$emit('confirm')
  await flushPromises()
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  mocks.releaseImpl = async () => undefined
  mocks.destroyImpl = async () => undefined
  mocks.rcloneRestoreImpl = async () => undefined
  mocks.logQueries = []
  document.body.innerHTML = ''
  // SegmentedTabs 带 hash-prefix：分区会写进 URL hash，而 jsdom 的 location 在同一
  // 文件内跨用例共享——不清掉的话上一个用例切到的分区会渗进下一个用例的初始状态
  // （实拍：保留策略用例切完后，后续用例一挂载就停在「保留策略」，行内按钮全没了）。
  window.location.hash = ''
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('归档页：保留策略布局（与审计页一致）', () => {
  it('三个保留天数：标签置顶 + 数字框不带步进按钮', async () => {
    const w = await mountAndSettle()
    await switchTab(w, 'retention')

    const numbers = w.findAllComponents(NInputNumber)
    expect(numbers.length).toBe(3)
    for (const number of numbers) {
      // stepper 是"上下箭头"两枚按钮，审计页同款一律关掉
      expect(number.props('showButton')).toBe(false)
    }
    w.unmount()
  })
})

describe('归档页：释放 ID / 销毁数据的执行反馈', () => {
  it('确认后弹窗不关闭，先呈现「执行中」', async () => {
    let release: (() => void) | null = null
    mocks.releaseImpl = () => new Promise<void>((resolve) => {
      release = resolve
    })
    const w = await mountAndSettle()
    await clickRowAction(w, '释放 ID')
    await confirmGate(w, 0)
    await settle()

    expect(modalText()).toContain('正在释放')
    release!()
    await flushPromises()
    w.unmount()
  })

  it('完成后在弹窗内呈现成功结果，且不自动关门（用户看完自己关）', async () => {
    const w = await mountAndSettle()
    await clickRowAction(w, '释放 ID')
    await confirmGate(w, 0)
    await settle()

    expect(modalText()).toContain('已释放')
    // 这是本次修复的核心：改前 ConfirmGate 点确认即 close()，用户根本看不到结果
    expect(w.findComponent(ConfirmGate).props('show')).toBe(true)
    w.unmount()
  })

  it('失败时三个动作共用同一把失败文案（错误呈现一致）', async () => {
    mocks.releaseImpl = async () => {
      throw new Error('仓库 id 不匹配')
    }
    const w = await mountAndSettle()
    await clickRowAction(w, '释放 ID')
    await confirmGate(w, 0)
    await settle()

    expect(modalText()).toContain('执行失败')
    expect(modalText()).toContain('仓库 id 不匹配')
    w.unmount()
  })

  it('销毁数据同样走执行中 → 结果（与释放 ID 同款）', async () => {
    const w = await mountAndSettle()
    await clickRowAction(w, '销毁数据')
    await confirmGate(w, 1)
    await settle()

    expect(modalText()).toContain('已销毁')
    w.unmount()
  })
})

describe('归档页：rclone 恢复写回的执行反馈', () => {
  it('与释放/销毁同款确认门：点「恢复写回」先开预览，确认后才执行中→结果', async () => {
    let done: (() => void) | null = null
    mocks.rcloneRestoreImpl = () => new Promise<void>((resolve) => {
      done = resolve
    })
    const w = await mountAndSettle()
    await switchTab(w, 'rclone')

    const btn = [...w.element.querySelectorAll('button')].find((el) =>
      (el.textContent ?? '').includes('恢复写回'),
    ) as HTMLButtonElement | undefined
    expect(btn).toBeTruthy()
    btn!.click()
    await settle()

    // 进确认门第 1 步（预览），尚未执行——与释放/销毁同一入口
    expect(modalText()).toContain('恢复写回配置')
    expect(modalText()).not.toContain('正在写回')

    // 确认（第三个确认门：释放=0 销毁=1 恢复写回=2）
    await confirmGate(w, 2)
    await settle()
    expect(modalText()).toContain('正在写回')

    done!()
    await flushPromises()
    expect(modalText()).toContain('已写回')
    w.unmount()
  })

  it('写回失败时呈现失败文案（与释放/销毁同一把）', async () => {
    mocks.rcloneRestoreImpl = async () => {
      throw new Error('同名远端已存在')
    }
    const w = await mountAndSettle()
    await switchTab(w, 'rclone')
    const btn = [...w.element.querySelectorAll('button')].find((el) =>
      (el.textContent ?? '').includes('恢复写回'),
    ) as HTMLButtonElement | undefined
    btn!.click()
    await settle()
    await confirmGate(w, 2)
    await settle()

    expect(modalText()).toContain('执行失败')
    expect(modalText()).toContain('同名远端已存在')
    w.unmount()
  })
})

describe('归档页：操作日志', () => {
  it('两个分区各有一个「操作日志」入口，且按动作清单查询（不另造流水）', async () => {
    const w = await mountAndSettle()
    await switchTab(w, 'repos')

    const open = [...w.element.querySelectorAll('button')].filter((el) =>
      (el.textContent ?? '').includes('操作日志'),
    )
    expect(open.length).toBe(1) // 仓库分区（当前 tab）

    open[0]!.click()
    await settle()
    expect(mocks.logQueries.length).toBe(1)
    expect(mocks.logQueries[0]).toEqual([
      'repo.archive',
      'repo.restore',
      'repo.purge',
      'repo.id_release',
    ])

    await switchTab(w, 'rclone')
    const rcloneOpen = [...w.element.querySelectorAll('button')].filter((el) =>
      (el.textContent ?? '').includes('操作日志'),
    )
    expect(rcloneOpen.length).toBe(1)
    rcloneOpen[0]!.click()
    await settle()
    expect(mocks.logQueries.at(-1)).toEqual(['rclone.config_restore'])
    w.unmount()
  })
})
