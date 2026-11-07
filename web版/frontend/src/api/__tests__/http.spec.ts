/**
 * http 层单元测试：重点锁定 issue 1 缺陷——调用方显式传入的 `X-Repo-Id` 必须能覆盖
 * http 层默认注入的「当前仓库」路由头。
 *
 * 背景：快照页从归档页跳转归档仓时，listSnapshots 传 `headers: { 'X-Repo-Id': 归档仓 id }`，
 * 后端 app/dependencies.py::current_repo_id 只认这个头来路由多仓库。此前 http.ts 把默认
 * `'X-Repo-Id': currentRepoId` 写在 `...headers` 展开之后，调用方的覆盖被吞掉，导致归档仓
 * 快照只读视图回显的是「当前仓」而非「归档仓」。修复后调用方头必须胜出。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { request, setAuthHandlers } from '@/api/http'
import { useUiStore } from '@/stores/ui'

function makeResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

describe('http X-Repo-Id 路由头', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    useUiStore().setCurrentRepoId('default')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('调用方传入 X-Repo-Id 时覆盖默认当前仓 id（issue 1 回归）', async () => {
    const fetchMock = vi.fn(async () => makeResponse([], true, 200))
    vi.stubGlobal('fetch', fetchMock)

    useUiStore().setCurrentRepoId('active-repo')
    await request('/api/snapshots', { headers: { 'X-Repo-Id': 'archived-123' } })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, init] = fetchMock.mock.calls[0] as unknown as [unknown, RequestInit | undefined]
    const headers = (init?.headers ?? {}) as Record<string, string>
    expect(headers['X-Repo-Id']).toBe('archived-123')
  })

  it('未传 X-Repo-Id 时回落为当前激活仓 id', async () => {
    const fetchMock = vi.fn(async () => makeResponse([], true, 200))
    vi.stubGlobal('fetch', fetchMock)

    useUiStore().setCurrentRepoId('active-repo')
    await request('/api/snapshots')

    const [, init] = fetchMock.mock.calls[0] as unknown as [unknown, RequestInit | undefined]
    const headers = (init?.headers ?? {}) as Record<string, string>
    expect(headers['X-Repo-Id']).toBe('active-repo')
  })

  it('调用方传其它头时不被破坏，且 X-Repo-Id 仍可被覆盖', async () => {
    const fetchMock = vi.fn(async () => makeResponse([], true, 200))
    vi.stubGlobal('fetch', fetchMock)

    useUiStore().setCurrentRepoId('active-repo')
    await request('/api/snapshots', { headers: { 'X-Repo-Id': 'archived-9', Accept: 'application/json' } })

    const [, init] = fetchMock.mock.calls[0] as unknown as [unknown, RequestInit | undefined]
    const headers = (init?.headers ?? {}) as Record<string, string>
    expect(headers['X-Repo-Id']).toBe('archived-9')
    expect(headers['Accept']).toBe('application/json')
  })

  it('当前仓 404 REPO_NOT_FOUND 时自动切回 default（删除/释放后 localStorage 残留兜底）', async () => {
    const fetchMock = vi.fn(async () =>
      makeResponse({ code: 'REPO_NOT_FOUND', message: "仓库 'r261102-n3d8a5' 不存在" }, false, 404),
    )
    vi.stubGlobal('fetch', fetchMock)

    useUiStore().setCurrentRepoId('r261102-n3d8a5')
    await expect(request('/api/repos/r261102-n3d8a5/status')).rejects.toThrow()
    expect(useUiStore().currentRepoId).toBe('default')
  })

  it('非当前仓的 404 REPO_NOT_FOUND 不触碰当前仓选择（快照页看归档仓等显式覆盖）', async () => {
    const fetchMock = vi.fn(async () =>
      makeResponse({ code: 'REPO_NOT_FOUND', message: '仓库不存在' }, false, 404),
    )
    vi.stubGlobal('fetch', fetchMock)

    useUiStore().setCurrentRepoId('active-repo')
    await expect(request('/api/repos', { headers: { 'X-Repo-Id': 'ghost-repo' } })).rejects.toThrow()
    expect(useUiStore().currentRepoId).toBe('active-repo')
  })
})

/**
 * 401 UNAUTHORIZED 分支 × 「抑制跳转」标志（W5 真机验收缺陷的回归护栏）。
 *
 * 缺陷现场：全站备份包导入成功后服务端清空 sessions，顶栏两个 15s 轮询随后拿到 401，
 * 照旧触发 clearMe + 跳 /login，约 7.7s 后把刚渲染的导入报告（含回滚点目录名——
 * 误导入后唯一回退依据）冲掉。修法：置位标志时只清态不跳转。
 *
 * 这里锁死两件事：① 置位时**不**调用 onUnauthorized 但**仍** clearMe；
 * ② 未置位时行为与修复前逐字一致（回归护栏，防止把普通 401 也吞掉）。
 */
describe('http 401 × 抑制鉴权跳转标志', () => {
  /** 当前 Pinia 实例上的 store（与 http.ts 内部 useUiStore() 同实例）。 */
  let ui: ReturnType<typeof useUiStore>
  let onUnauthorized: ReturnType<typeof vi.fn>
  /** 单调递增的假时钟（见 beforeEach 注释）。 */
  let clock = Date.now()

  beforeEach(() => {
    setActivePinia(createPinia())
    ui = useUiStore()
    ui.setMe({ authenticated: true, must_change_password: false, user: 'admin' })
    ui.setSuppressAuthRedirect(false)
    onUnauthorized = vi.fn()
    setAuthHandlers({ onUnauthorized })
    // redirectOnce 有 1s 节流（模块级 lastRedirectAt，按 Date.now 比较）：同一文件里连续
    // 两条用例往往只隔几毫秒，第二条会被节流压成「没跳」。这里把 Date.now 单调推高，
    // 让每条用例都从「距上次跳转已过去足够久」开始。
    // 注意不能用 vi.useFakeTimers()——afterEach 恢复真实时钟后 now 会**变小**，
    // lastRedirectAt 反而落在未来（实测让第二条用例假失败）。
    clock += 60_000
    vi.spyOn(Date, 'now').mockReturnValue(clock)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    setAuthHandlers({})
  })

  function stubUnauthorized(): void {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => makeResponse({ code: 'UNAUTHORIZED', message: '未登录或会话已过期' }, false, 401)),
    )
  }

  it('标志置位时：401 不跳转，但本地会话态仍被清空', async () => {
    ui.setSuppressAuthRedirect(true)
    stubUnauthorized()

    await expect(request('/api/repos/default/status')).rejects.toThrow()

    expect(onUnauthorized).not.toHaveBeenCalled()
    // 关键：不是「什么都不做」——界面不能继续显示「已登录」
    expect(ui.me).toBeNull()
  })

  it('标志未置位时：401 行为与修复前一致（先清态再跳转）', async () => {
    ui.setSuppressAuthRedirect(false)
    stubUnauthorized()

    await expect(request('/api/jobs?status=running')).rejects.toThrow()

    expect(ui.me).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })
})
