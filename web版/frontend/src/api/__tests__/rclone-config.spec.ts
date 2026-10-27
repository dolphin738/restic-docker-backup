/**
 * rclone 配置管理 API 出口测试（**请求体序列化护栏**）。
 *
 * 背景（2026-10-27 线上缺陷）：`createRcloneConfig` / `updateRcloneConfig` 曾写成
 * `body: JSON.stringify(payload)`，而 `http.request` 内部已统一 `JSON.stringify(body)`
 * —— 双重序列化让实际请求体变成**字符串字面量** `"{\"type\":...}"`，后端 pydantic
 * 期望对象 → 422「参数校验失败」，UI 上表现为「填好了点生成，报参数校验」。
 *
 * 为什么既有测试没抓到：`api/__tests__/restore.spec.ts` 等把 `@/api/http` 整个 mock
 * 掉，断言的是传给 `request` 的**原始对象**，序列化层从未被执行。
 * 本文件因此**不 mock http**，改为 stub `global.fetch`，断言真实发出的 `init.body`
 * 解析后是对象（而非字符串）——这是唯一能锁住该缺陷的观测点。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createRcloneConfig, deleteRcloneConfig, updateRcloneConfig } from '@/api/repo'

const fetchMock = vi.fn()

/** 取最近一次 fetch 的请求体（已按 JSON 解析）。 */
function lastBody(): unknown {
  const call = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
  const init = call?.[1] as RequestInit | undefined
  return JSON.parse(String(init?.body))
}

/** 取最近一次 fetch 的 method。 */
function lastMethod(): string | undefined {
  const call = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
  return (call?.[1] as RequestInit | undefined)?.method
}

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ remote: 'x', type: 'crypt', conf_path: '/c', action: 'create', remotes_after: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('rclone 配置写入 API 的请求体', () => {
  it('create：body 解析后是对象（不是被二次序列化的字符串）', async () => {
    await createRcloneConfig('mycrypt', {
      type: 'crypt',
      params: { remote: 'mywebdav:bucket', password: 'p' },
      store_form: 'obscured',
      value_obscured: false,
    })

    const body = lastBody()
    expect(typeof body).toBe('object')
    expect(body).toEqual({
      type: 'crypt',
      params: { remote: 'mywebdav:bucket', password: 'p' },
      store_form: 'obscured',
      value_obscured: false,
    })
    expect(lastMethod()).toBe('POST')
  })

  it('update：body 解析后是对象且 method=PUT', async () => {
    await updateRcloneConfig('mycrypt', {
      type: 'crypt',
      params: { filename_encoding: 'base64' },
      store_form: 'plain',
      value_obscured: true,
    })

    const body = lastBody()
    expect(typeof body).toBe('object')
    expect((body as Record<string, unknown>)['store_form']).toBe('plain')
    expect((body as Record<string, unknown>)['value_obscured']).toBe(true)
    expect(lastMethod()).toBe('PUT')
  })

  it('create：远端名做 URL 编码（名字含特殊字符不会串路径）', async () => {
    await createRcloneConfig('my remote', { type: 'webdav', params: { url: 'https://x' } })
    const url = String(fetchMock.mock.calls[fetchMock.mock.calls.length - 1]?.[0])
    expect(url).toContain('/api/rclone/config/my%20remote')
  })

  it('delete：无请求体、method=DELETE', async () => {
    await deleteRcloneConfig('mycrypt')
    const init = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]?.[1] as RequestInit | undefined
    expect(init?.body).toBeUndefined()
    expect(lastMethod()).toBe('DELETE')
  })
})
