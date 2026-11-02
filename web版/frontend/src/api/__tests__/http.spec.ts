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

import { request } from '@/api/http'
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
