/**
 * 全站备份 / 恢复 API 出口单元测试（方案 §10.1 W5；红线「请求只经 src/api/ 单一出口」的前端看守）。
 *
 * 为什么值得单独锁：本功能是**整机替换**语义，请求体一旦漏字段或被前端「顺手改造」
 * （例如漏传 acknowledge、把 mode 写成缺省、或自己拼一个 fetch 绕过 http 层），
 * 后果要么是导入被服务端 409 拒绝（用户卡死），要么绕开 cookie/Origin 鉴权面（S4/S5）。
 *
 * mock 要点：只 mock ./http 的 request，断言**外部可观测的请求路径 / 方法 / 请求体**——
 * 逐字段断言而非快照，确保「acknowledge 逐字透传」「模式恒为 replace」这类契约被锁住。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { request } from '@/api/http'
import {
  LEGACY_CONFIG_PACK_FORMAT,
  SITE_PACK_FORMAT,
  exportSiteBackup,
  importSiteBackup,
  inspectSiteBackup,
} from '@/api/siteBackup'
import type { SiteBackupPack, SiteImportReport } from '@/api/types'

vi.mock('@/api/http', () => ({
  request: vi.fn(),
}))

const requestMock = vi.mocked(request)

/** 最小合法包（§2.2：四个明文头字段 + payload；前端只透传，不解释）。 */
function pack(): SiteBackupPack {
  return {
    format: SITE_PACK_FORMAT,
    version: 1,
    created_at: '2026-11-04T02:11:07Z',
    encryption: { algo: 'PBKDF2-HMAC-SHA256', iterations: 600000 },
    payload: 'ZmFrZQ==',
  }
}

/** 取最近一次 request 调用的请求体（request 被 mock，JSON 序列化未发生 → 取原始对象）。 */
function lastBody(): Record<string, unknown> {
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

afterEach(() => {
  vi.clearAllMocks()
})

describe('exportSiteBackup — E69 导出', () => {
  beforeEach(() => {
    requestMock.mockResolvedValue(pack() as never)
  })

  it('走 /api/site-backup/export，POST，口令与 rclone 勾选原样进请求体', async () => {
    await exportSiteBackup({ password: 'a-very-long-password', include_rclone_conf: true })

    const [path, options] = requestMock.mock.calls[0]!
    expect(path).toBe('/api/site-backup/export')
    expect(options?.method).toBe('POST')

    const body = lastBody()
    expect(body['password']).toBe('a-very-long-password')
    expect(body['include_rclone_conf']).toBe(true)
    expect(Object.keys(body).sort()).toEqual(['include_rclone_conf', 'password'])
  })

  it('include_rclone_conf=false 时明确下传 false（不是省略，缺省由服务端填 true）', async () => {
    await exportSiteBackup({ password: 'a-very-long-password', include_rclone_conf: false })

    const body = lastBody()
    expect(Object.prototype.hasOwnProperty.call(body, 'include_rclone_conf')).toBe(true)
    expect(body['include_rclone_conf']).toBe(false)
  })
})

describe('inspectSiteBackup — E70 预检（只读）', () => {
  beforeEach(() => {
    requestMock.mockResolvedValue({ pack: {}, target: {}, blockers: [] } as never)
  })

  it('走 /api/site-backup/inspect，POST，整包透传 + 口令', async () => {
    const source = pack()
    await inspectSiteBackup({ pack: source, password: 'pw-for-inspect' })

    const [path, options] = requestMock.mock.calls[0]!
    expect(path).toBe('/api/site-backup/inspect')
    expect(options?.method).toBe('POST')

    const body = lastBody()
    expect(body['pack']).toEqual(source)
    expect(body['password']).toBe('pw-for-inspect')
    // 只读端点不得夹带落地语义字段
    expect(body).not.toHaveProperty('mode')
    expect(body).not.toHaveProperty('acknowledge')
    expect(Object.keys(body).sort()).toEqual(['pack', 'password'])
  })
})

describe('importSiteBackup — E71 整机替换', () => {
  beforeEach(() => {
    requestMock.mockResolvedValue({
      applied: { repos: 1, tasks: 0, config: 0, forms: 0, passfiles: 1, rclone_conf: 0, rclone_pass: 0 },
      normalized_env_keys: [],
      warnings: [],
      rollback_dir: '20261104T021107Z-ab12cd',
      logged_out: true,
    } as unknown as SiteImportReport as never)
  })

  it('走 /api/site-backup/import，POST；mode 恒为 replace，acknowledge 逐字透传包外层 created_at', async () => {
    const source = pack()
    await importSiteBackup({
      pack: source,
      password: 'pw-for-import',
      mode: 'replace',
      acknowledge: source.created_at,
    })

    const [path, options] = requestMock.mock.calls[0]!
    expect(path).toBe('/api/site-backup/import')
    expect(options?.method).toBe('POST')

    const body = lastBody()
    expect(body['mode']).toBe('replace')
    // §4.2：acknowledge 必须逐字等于**包外层** created_at（不是载荷内的）
    expect(body['acknowledge']).toBe('2026-11-04T02:11:07Z')
    expect(body['pack']).toEqual(source)
    expect(Object.keys(body).sort()).toEqual(['acknowledge', 'mode', 'pack', 'password'])
  })

  it('响应原样返回报告（applied / rollback_dir / logged_out 不被前端改写）', async () => {
    const data = await importSiteBackup({
      pack: pack(),
      password: 'pw',
      mode: 'replace',
      acknowledge: '2026-11-04T02:11:07Z',
    })

    expect(data.applied['repos']).toBe(1)
    expect(data.rollback_dir).toBe('20261104T021107Z-ab12cd')
    expect(data.logged_out).toBe(true)
  })
})

describe('包格式标识（§2.2：与旧配置迁移包区分，D6 不做兼容）', () => {
  it('全站包与旧配置迁移包是两个字面量，不得混用', () => {
    expect(SITE_PACK_FORMAT).toBe('restic-web-site-backup')
    expect(LEGACY_CONFIG_PACK_FORMAT).toBe('restic-web-config-export')
    expect(SITE_PACK_FORMAT).not.toBe(LEGACY_CONFIG_PACK_FORMAT)
  })
})
