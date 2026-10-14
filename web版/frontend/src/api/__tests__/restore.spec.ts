/**
 * 恢复 API 出口单元测试（R-015 零回归护栏，红线 S7 要件③的前端看守）。
 *
 * 背景：`pages/restore/index.vue` 用「所选模式」决定 E19 请求体是否带覆盖语义字段——
 * 时间戳子目录路径一旦误传 `overwrite_mode`，后端判 422，用户卡死。此前前端侧
 * `src/api/**` 与 `pages/restore/*` 零单测，改页面极易回归且无人看守，故补本文件。
 *
 * mock 要点：`restore.ts` 只依赖 `./http` 的 `request`，故在模块级 mock 掉 http 出口，
 * 断言**外部可观测的请求体**——逐字段断言（非快照），确保「无覆盖语义字段」这类
 * 否定性契约也能被锁住。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { request } from '@/api/http'
import {
  INPLACE_TARGET_ROOT,
  RESTORE_TARGET_ROOT,
  previewRestore,
  restoreSnapshot,
} from '@/api/restore'
import type { RestorePreviewResponse, RestoreResponse } from '@/api/types'

vi.mock('@/api/http', () => ({
  request: vi.fn(),
}))

const requestMock = vi.mocked(request)

/** 64 位十六进制快照 id（restic 真实形态），前 8 位 = 'a1b2c3d4'。 */
const SNAPSHOT_ID = 'a1b2c3d4' + 'e5f6'.repeat(14)

/** 取最近一次 `request` 调用的请求体。
 *
 * 说明：`request` 被 mock 掉了，JSON 序列化发生在它的内部，故这里拿到的是**原始对象**
 * （字符串分支只为兼容 http 层实现改为自行序列化的情形）。逐字段断言基于该对象。 */
function lastRequestBody(): Record<string, unknown> {
  const call = requestMock.mock.calls[requestMock.mock.calls.length - 1]
  if (!call) {
    throw new Error('request 未被调用')
  }
  const body = call[1]?.body
  if (typeof body === 'string') {
    return JSON.parse(body) as Record<string, unknown>
  }
  return (body ?? {}) as Record<string, unknown>
}

function restoreResponse(): RestoreResponse {
  return { job_id: 'job-1', restore_target: `${RESTORE_TARGET_ROOT}/etc-20261015-0138` }
}

function previewResponse(overrides: Partial<RestorePreviewResponse> = {}): RestorePreviewResponse {
  return {
    overwrite_count: 0,
    items: [],
    total_bytes: 0,
    next_cursor: null,
    truncated: false,
    ...overrides,
  }
}

describe('restoreSnapshot — 时间戳子目录流（零覆盖语义）', () => {
  beforeEach(() => {
    requestMock.mockResolvedValue(restoreResponse() as never)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('target 落在 /restore 时不带任何覆盖语义字段', async () => {
    await restoreSnapshot(SNAPSHOT_ID, RESTORE_TARGET_ROOT)

    const [path, options] = requestMock.mock.calls[0]!
    expect(path).toBe('/api/restore')
    expect(options?.method).toBe('POST')

    const body = lastRequestBody()
    // 逐字段断言：请求体只有 E19 的三个既有字段
    expect(body['snapshot_id']).toBe(SNAPSHOT_ID)
    expect(body['target']).toBe('/restore')
    expect(body).not.toHaveProperty('paths')
    // 否定性契约：误传任一覆盖字段 → 后端 422，用户卡死
    expect(body).not.toHaveProperty('overwrite_mode')
    expect(body).not.toHaveProperty('keyword')
    expect(Object.keys(body).sort()).toEqual(['snapshot_id', 'target'])
  })

  it('带 paths（B1 文件级恢复）时仍不带覆盖语义字段', async () => {
    await restoreSnapshot(SNAPSHOT_ID, RESTORE_TARGET_ROOT, ['/etc', '/home/u/x'])

    const body = lastRequestBody()
    expect(body['paths']).toEqual(['/etc', '/home/u/x'])
    expect(body).not.toHaveProperty('overwrite_mode')
    expect(body).not.toHaveProperty('keyword')
    expect(Object.keys(body).sort()).toEqual(['paths', 'snapshot_id', 'target'])
  })

  it('paths 为空数组时不下传（走服务端整快照缺省分支）', async () => {
    await restoreSnapshot(SNAPSHOT_ID, RESTORE_TARGET_ROOT, [])

    const body = lastRequestBody()
    expect(body).not.toHaveProperty('paths')
    expect(body).not.toHaveProperty('overwrite_mode')
    expect(body).not.toHaveProperty('keyword')
  })
})

describe('restoreSnapshot — 原位恢复流（R-015）', () => {
  beforeEach(() => {
    requestMock.mockResolvedValue(restoreResponse() as never)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('固定 overwrite_mode=always 且 keyword = 快照短 id 前 8 位', async () => {
    await restoreSnapshot(SNAPSHOT_ID, `${INPLACE_TARGET_ROOT}/etc/nginx`, undefined, {
      overwrite_mode: 'always',
      keyword: SNAPSHOT_ID.slice(0, 8),
    })

    const [path, options] = requestMock.mock.calls[0]!
    expect(path).toBe('/api/restore')
    expect(options?.method).toBe('POST')

    const body = lastRequestBody()
    expect(body['snapshot_id']).toBe(SNAPSHOT_ID)
    expect(body['target']).toBe('/host/etc/nginx')
    // 要件③：模式恒为 always，界面无模式选择项
    expect(body['overwrite_mode']).toBe('always')
    // Q2 裁决：keyword = 快照短 id 前 8 位，后端二次校验
    expect(body['keyword']).toBe(SNAPSHOT_ID.slice(0, 8))
    expect(body['keyword']).toBe('a1b2c3d4')
    expect(body).not.toHaveProperty('paths')
  })

  it('原位流下 paths 与覆盖字段可共存（B1 勾选文件后仍走原位写回）', async () => {
    await restoreSnapshot(SNAPSHOT_ID, `${INPLACE_TARGET_ROOT}/etc/nginx`, ['/etc/nginx/conf.d'], {
      overwrite_mode: 'always',
      keyword: SNAPSHOT_ID.slice(0, 8),
    })

    const body = lastRequestBody()
    expect(body['paths']).toEqual(['/etc/nginx/conf.d'])
    expect(body['overwrite_mode']).toBe('always')
    expect(body['keyword']).toBe('a1b2c3d4')
  })
})

describe('previewRestore — E20 游标分页', () => {
  beforeEach(() => {
    requestMock.mockResolvedValue(previewResponse() as never)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('首页 cursor 传 null（不省略字段）', async () => {
    await previewRestore(SNAPSHOT_ID, `${INPLACE_TARGET_ROOT}/etc`)

    const [path, options] = requestMock.mock.calls[0]!
    expect(path).toBe('/api/restore/preview')
    expect(options?.method).toBe('POST')

    const body = lastRequestBody()
    expect(body['snapshot_id']).toBe(SNAPSHOT_ID)
    expect(body['target']).toBe('/host/etc')
    expect(Object.prototype.hasOwnProperty.call(body, 'cursor')).toBe(true)
    expect(body['cursor']).toBeNull()
    expect(body).not.toHaveProperty('limit')
  })

  it('续拉时原样透传 cursor 与 limit（不解析不自造）', async () => {
    const opaque = 'eyJvZmZzZXQiOjIwMH0'
    await previewRestore(SNAPSHOT_ID, `${INPLACE_TARGET_ROOT}/etc`, {
      limit: 200,
      cursor: opaque,
    })

    const body = lastRequestBody()
    expect(body['limit']).toBe(200)
    expect(body['cursor']).toBe(opaque)
  })

  it('响应原样返回 overwrite_count / total_bytes / next_cursor / truncated', async () => {
    requestMock.mockResolvedValue(
      previewResponse({
        overwrite_count: 3,
        total_bytes: 4096,
        next_cursor: 'eyJvZmZzZXQiOjIwMH0',
        truncated: true,
        items: [
          {
            path: '/host/etc/nginx/nginx.conf',
            display_path: '/etc/nginx/nginx.conf',
            snapshot_mtime: '2026-10-14T23:09:20.702+08:00',
            disk_mtime: '2026-10-15T01:02:03.000+08:00',
            snapshot_size: 1024,
            disk_size: 2048,
          },
        ],
      }) as never,
    )

    const data = await previewRestore(SNAPSHOT_ID, `${INPLACE_TARGET_ROOT}/etc`, { limit: 200 })

    expect(data.overwrite_count).toBe(3)
    expect(data.total_bytes).toBe(4096)
    expect(data.truncated).toBe(true)
    expect(data.next_cursor).toBe('eyJvZmZzZXQiOjIwMH0')
    expect(data.items[0]?.display_path).toBe('/etc/nginx/nginx.conf')
    expect(data.items[0]?.snapshot_mtime).toBe('2026-10-14T23:09:20.702+08:00')
    expect(data.items[0]?.disk_mtime).toBe('2026-10-15T01:02:03.000+08:00')
    expect(data.items[0]?.snapshot_size).toBe(1024)
    expect(data.items[0]?.disk_size).toBe(2048)
  })
})
